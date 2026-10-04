"""SMTP 邮件后端。

刻意保持最小实现：只做"发出去"这一件事，重试与队列留给运维层（systemd 重启、
日志告警）。社团规模下不值得引入任务队列。
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import Settings

logger = logging.getLogger(__name__)


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        # SMTP 本身没有幂等键这一说（去重要靠 Message-ID）。接受但忽略，见端口说明
        settings = self._settings
        if not settings.SMTP_HOST:
            raise RuntimeError("EMAIL_BACKEND=smtp 但未配置 SMTP_HOST")

        message = EmailMessage()
        message["From"] = settings.EMAIL_FROM
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if html:
            # `subtype` 必须写清楚：不写的话默认还是 text/plain，客户端会把整段
            # HTML 当正文原样显示出来
            message.add_alternative(html, subtype="html")

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_STARTTLS:
                smtp.starttls()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)

        logger.info("邮件已发送至 %s（主题：%s）", to, subject)


__all__ = ["SmtpEmailSender"]
