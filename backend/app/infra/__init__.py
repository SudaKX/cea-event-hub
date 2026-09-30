"""可替换基础设施的实现。

每个实现都对应 `core/ports.py` 里的一个 Protocol，由 `build_*` 工厂按配置选择。
service 只依赖端口，因此换实现不需要动业务代码。
"""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.core.ports import EmailSender, RateLimiter
from app.infra.email_console import ConsoleEmailSender
from app.infra.email_smtp import SmtpEmailSender
from app.infra.ratelimit_memory import InMemoryRateLimiter

logger = logging.getLogger(__name__)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """构造限流器。

    当前只有进程内实现。要换成 Redis 时改这里即可。
    """
    return InMemoryRateLimiter()


def build_email_sender(settings: Settings) -> EmailSender:
    """按 `EMAIL_BACKEND` 选择邮件后端。

    默认 console：未配置 SMTP 时注册与找回流程仍然完整可用，只是邮件落在日志里。
    """
    backend = (settings.EMAIL_BACKEND or "console").strip().lower()
    if backend == "smtp":
        if not settings.SMTP_HOST:
            logger.warning(
                "EMAIL_BACKEND=smtp 但 SMTP_HOST 为空，回退到 console 后端"
            )
            return ConsoleEmailSender()
        return SmtpEmailSender(settings)
    return ConsoleEmailSender()


__all__ = [
    "ConsoleEmailSender",
    "InMemoryRateLimiter",
    "SmtpEmailSender",
    "build_email_sender",
    "build_rate_limiter",
]
