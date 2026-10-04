"""Resend 邮件后端。

**为什么用同步的 `Emails.send()` 而不是 `send_async`。** 本项目的端点全是同步
`def`，FastAPI 已经把它们放进线程池执行，因此这里再引入一个 async 边界没有任何
收益，只会多一层桥接。（`.ref/resend.md` 建议 async 代码用 `send_async` —— 那条
针对的是真正的 async 上下文，这里不是。）

三个容易写错的地方，代码里都标了出来：

1. `send()` 在失败时**抛异常**，不返回错误对象 —— 只检查返回值等于没检查
2. 幂等键是 `send(params, options)` 的**第二个参数**，不是 `params` 里的字段
3. 缺必填参数抛的是普通 `ValueError` 而不是 `ResendError`，只捕获后者会漏
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ResendEmailSender:
    """经 Resend 的 HTTP API 发信。

    **密钥缺失时应用照常启动，但每次发信都会抛错** —— 启动时先告警一次，发信时
    由 `_safe_send` 记成 exception 级别的日志，因此共有两处响亮的信号。

    为什么不索性拒绝启动：邮件不是这个应用唯一的依赖 —— 浏览活动、审核提交、
    管理员签发重置令牌都不需要它（design.md 决策 12）。为一个待填的密钥让整个
    环境起不来，代价大于收益。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key = (settings.RESEND_API_KEY or "").strip()
        if not self._key:
            logger.warning(
                "EMAIL_BACKEND=resend 但 RESEND_API_KEY 为空 —— 发信会失败。"
                "请在 backend/.env 里填上密钥，或把 EMAIL_BACKEND 改回 console。"
            )
        self._module: Any = None

    def _resend(self) -> Any:
        """延迟导入。

        只有真的选了这个后端才需要那个包 —— console 与 smtp 部署不该被一个用不到
        的依赖卡住启动。
        """
        if self._module is None:
            if not self._key:
                # 抛在这里而不是 __init__：应用照常启动，只在真的要用邮件时才失败
                raise RuntimeError(
                    "EMAIL_BACKEND=resend 但 RESEND_API_KEY 为空。"
                    "请在 backend/.env 里填上密钥，或把 EMAIL_BACKEND 改回 console。"
                )
            try:
                import resend
            except ImportError as exc:  # pragma: no cover - 取决于安装方式
                raise RuntimeError(
                    "EMAIL_BACKEND=resend 需要 resend 包："
                    "pip install 'cea-event-hub[resend]'"
                ) from exc
            resend.api_key = self._key
            self._module = resend
        return self._module

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        resend = self._resend()
        errors = _errors()

        params: dict[str, Any] = {
            "from": self._settings.EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "text": body,
        }
        if html:
            # 同时给 text 与 html：Resend 会组装成 multipart/alternative，
            # 由客户端挑它能显示的那一版
            params["html"] = html

        try:
            if idempotency_key:
                # **第二个参数**。塞进 params 会被当成未知字段，去重也就不会发生
                resend.Emails.send(params, {"idempotency_key": idempotency_key})
            else:
                resend.Emails.send(params)
        except errors["resend_error"] as error:
            # 该 SDK **靠抛异常报告失败**，不返回错误对象。这里记下可判别的信息再
            # 抛出去，由调用方决定要不要让整个请求失败（`_safe_send` 会吞掉它，
            # 因为"信没发出去"不该让注册请求本身失败）
            logger.warning(
                "Resend 发信失败：收件人=%s 主题=%s 类型=%s 详情=%s",
                to,
                subject,
                type(error).__name__,
                error,
            )
            raise
        except ValueError as error:
            # 缺必填参数抛的是普通 ValueError，**不是** ResendError。
            # 只捕获后者的话，这类错误会以 500 的形式冒到用户面前
            logger.error("Resend 拒绝了这次发送（参数不合法）：%s", error)
            raise

        logger.info("邮件已通过 Resend 发送至 %s（主题：%s）", to, subject)


def _errors() -> dict[str, type[BaseException]]:
    """取异常基类。

    延迟导入并做一次兜底：`resend.exceptions` 若在将来改名，这里应当退化为
    "没有可捕获的类型"（于是异常直接冒泡，行为仍然正确），而不是在导入期炸掉。
    """
    from resend.exceptions import ResendError

    return {"resend_error": ResendError}


__all__ = ["ResendEmailSender"]
