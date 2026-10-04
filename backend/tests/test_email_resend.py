"""装配与错误处理：任务 16.5。

**不发真邮件。** 这里用替身模块顶掉 `resend`，验的是"我们怎么调它"——参数形状、
幂等键的位置、异常有没有被接住。真投递由运维在部署环境里验，不是单元测试的事。
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.core.config import Settings
from app.infra import build_email_sender
from app.infra.email_console import ConsoleEmailSender
from app.infra.email_resend import ResendEmailSender


class FakeResendError(Exception):
    """顶替 `resend.exceptions.ResendError`。"""


class FakeEmails:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[tuple[Any, ...]]] = []
        self._raises = raises

    def send(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        self.calls.append((args, kwargs))  # type: ignore[arg-type]
        if self._raises is not None:
            raise self._raises
        return {"id": "fake"}


@pytest.fixture
def fake_resend(monkeypatch: pytest.MonkeyPatch):
    """把 `resend` 换成替身，并记录 `api_key` 有没有被设上。"""
    module = types.ModuleType("resend")
    module.api_key = None  # type: ignore[attr-defined]

    emails = FakeEmails()
    module.Emails = emails  # type: ignore[attr-defined]

    exceptions = types.ModuleType("resend.exceptions")
    exceptions.ResendError = FakeResendError  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "resend", module)
    monkeypatch.setitem(sys.modules, "resend.exceptions", exceptions)
    return module, emails


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "EMAIL_BACKEND": "resend",
        "RESEND_API_KEY": "re_test_key",
        "EMAIL_FROM": "CEA <noreply@cea.example>",
    }
    base.update(overrides)
    return Settings(**base)


class TestAssembly:
    def test_resend_backend_is_selected(self, fake_resend) -> None:
        sender = build_email_sender(make_settings())
        assert isinstance(sender, ResendEmailSender)

    def test_console_stays_the_default(self) -> None:
        assert isinstance(
            build_email_sender(Settings(EMAIL_BACKEND="console")), ConsoleEmailSender
        )

    def test_missing_key_warns_but_does_not_block_startup(self, caplog) -> None:
        """密钥缺失**不阻止启动**，但发信必然失败。

        拒绝启动的代价是把整个环境拖下水 —— 而浏览活动、审核提交、管理员签发
        重置令牌都不需要邮件。这里把"两处信号"都钉住：启动时告警、发信时抛错。
        """
        import logging

        with caplog.at_level(logging.WARNING):
            sender = build_email_sender(make_settings(RESEND_API_KEY=""))
        assert isinstance(sender, ResendEmailSender)
        assert "RESEND_API_KEY 为空" in caplog.text

        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            sender.send(to="a@example.com", subject="s", body="b")

    def test_api_key_is_handed_to_the_sdk(self, fake_resend) -> None:
        module, _emails = fake_resend
        sender = build_email_sender(make_settings(RESEND_API_KEY="re_abc"))
        sender.send(to="a@example.com", subject="s", body="b")
        assert module.api_key == "re_abc"


class TestSending:
    def test_params_carry_from_to_subject_and_body(self, fake_resend) -> None:
        _module, emails = fake_resend
        sender = build_email_sender(make_settings())

        sender.send(to="alice@example.com", subject="完成注册", body="正文")

        (args, _kwargs) = emails.calls[0]
        params = args[0]
        assert params["from"] == "CEA <noreply@cea.example>"
        # 收件人是**列表**：SDK 的 to 接受字符串或列表，统一用列表更稳
        assert params["to"] == ["alice@example.com"]
        assert params["subject"] == "完成注册"
        assert params["text"] == "正文"

    def test_html_goes_into_params_when_given(self, fake_resend) -> None:
        """同时给 text 与 html —— Resend 会组装成 multipart/alternative。

        只发 HTML 会被反垃圾系统扣分，纯文本客户端的读者还会看到一片空白。
        """
        _module, emails = fake_resend
        sender = build_email_sender(make_settings())

        sender.send(
            to="a@example.com", subject="s", body="纯文本", html="<p>富文本</p>"
        )

        (args, _kwargs) = emails.calls[0]
        assert args[0]["text"] == "纯文本"
        assert args[0]["html"] == "<p>富文本</p>"

    def test_no_html_key_when_absent(self, fake_resend) -> None:
        # 不给就别塞空字符串：那会让客户端显示一个空白的富文本版本
        _module, emails = fake_resend
        sender = build_email_sender(make_settings())
        sender.send(to="a@example.com", subject="s", body="纯文本")
        (args, _kwargs) = emails.calls[0]
        assert "html" not in args[0]

    def test_idempotency_key_is_the_second_argument(self, fake_resend) -> None:
        """**这条最容易写错。**

        键属于 `send(params, options)` 的第二个参数。塞进 `params` 不会报错，但去重
        也就静默地不会发生 —— 正是那种"看起来实现了"的错误。
        """
        _module, emails = fake_resend
        sender = build_email_sender(make_settings())

        sender.send(
            to="alice@example.com",
            subject="s",
            body="b",
            idempotency_key="registration-pending/42",
        )

        (args, _kwargs) = emails.calls[0]
        assert len(args) == 2, "幂等键必须作为第二个参数传，而不是塞进 params"
        assert args[1] == {"idempotency_key": "registration-pending/42"}
        assert "idempotency_key" not in args[0]

    def test_no_key_means_a_single_argument_call(self, fake_resend) -> None:
        _module, emails = fake_resend
        sender = build_email_sender(make_settings())
        sender.send(to="a@example.com", subject="s", body="b")
        (args, _kwargs) = emails.calls[0]
        assert len(args) == 1

    def test_resend_error_propagates(self, fake_resend) -> None:
        """异常必须冒出去，由调用方决定怎么处理。"""
        _module, emails = fake_resend
        emails._raises = FakeResendError("boom")
        sender = build_email_sender(make_settings())

        with pytest.raises(FakeResendError):
            sender.send(to="a@example.com", subject="s", body="b")

    def test_value_error_propagates_too(self, fake_resend) -> None:
        """缺必填参数抛的是普通 `ValueError`，**不是** `ResendError`。

        只捕获后者的话，这类错误会以 500 的形式冒到用户面前。这里断言它同样往上
        冒 —— 而不是被 `except ResendError` 漏过去之后失去上下文。
        """
        _module, emails = fake_resend
        emails._raises = ValueError("missing to")
        sender = build_email_sender(make_settings())

        with pytest.raises(ValueError):
            sender.send(to="a@example.com", subject="s", body="b")

    def test_the_module_is_imported_lazily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """没选 resend 后端时，那个包根本不该被导入。

        console 与 smtp 部署不该被一个用不到的依赖卡住启动。
        """
        monkeypatch.setitem(sys.modules, "resend", None)
        assert isinstance(
            build_email_sender(Settings(EMAIL_BACKEND="console")), ConsoleEmailSender
        )
