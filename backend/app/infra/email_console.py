"""开发用邮件后端：把邮件写到日志。

存在的意义是让注册、邮箱验证与口令找回的**全流程在没有 SMTP 的情况下也能跑通**。
没有它，未配置邮件就等于这些功能无法开发和测试。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConsoleEmailSender:
    def __init__(self, target: logging.Logger | None = None) -> None:
        self._logger = target or logger
        self.sent: list[tuple[str, str, str]] = []
        #: 收到的 HTML 版本。日志里只打纯文本（在终端里看 HTML 没有意义），
        #: 但留一份供测试断言"两个版本都发对了"
        self.htmls: list[str | None] = []

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        # 幂等键对"只写日志"没有意义，但**接受**它：端口是公共形状，实现之间不该
        # 在签名上分叉。带上键一起记，排查时能看到"键到底传没传对"
        self.sent.append((to, subject, body))
        self.htmls.append(html)
        self._logger.info(
            "\n---- 邮件（console 后端，未实际发送）----\n"
            "  收件人: %s\n"
            "  主题  : %s\n"
            "  幂等键: %s\n"
            "  HTML  : %s\n"
            "%s\n"
            "------------------------------------------",
            to,
            subject,
            idempotency_key or "（无）",
            f"{len(html)} 字节" if html else "（无）",
            "\n".join(f"  {line}" for line in body.splitlines()),
        )


__all__ = ["ConsoleEmailSender"]
