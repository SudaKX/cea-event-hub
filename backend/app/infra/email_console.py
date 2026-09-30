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

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))
        self._logger.info(
            "\n---- 邮件（console 后端，未实际发送）----\n"
            "  收件人: %s\n"
            "  主题  : %s\n"
            "%s\n"
            "------------------------------------------",
            to,
            subject,
            "\n".join(f"  {line}" for line in body.splitlines()),
        )


__all__ = ["ConsoleEmailSender"]
