"""可替换基础设施的实现。

每个实现都对应 `core/ports.py` 里的一个 Protocol，由 `build_*` 工厂按配置选择。
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.ports import RateLimiter
from app.infra.ratelimit_memory import InMemoryRateLimiter


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """构造限流器。

    当前只有进程内实现。要换成 Redis 时改这里即可，service 不需要知道。
    """
    return InMemoryRateLimiter()


__all__ = ["InMemoryRateLimiter", "build_rate_limiter"]
