"""进程内限流实现：滑动窗口日志。

**为什么是滑动窗口而不是固定窗口**：固定窗口在边界处允许 2×limit 挤在极短的
时间跨度内（窗口末尾打满 + 新窗口开头再打满），而它恰恰是最容易被脚本利用的
形态。滑动窗口按"最近 window_seconds 内有多少次命中"判定，没有这个缺口。

**为什么必须加锁**：FastAPI 的同步端点跑在线程池里，即使 `--workers 1`，
同一进程内仍有并发。无锁的 deque 操作会让计数丢失或重复。
"""

from __future__ import annotations

import threading
import time
from collections import deque

from app.core.ports import RateLimitDecision


class InMemoryRateLimiter:
    """键 -> 命中时间戳队列。

    时间源用 `time.monotonic()`：它不受系统时钟调整影响，而限流判定只关心
    时间间隔，不关心绝对时刻。
    """

    def __init__(self, *, max_keys: int = 20_000) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        #: 键数量上限。超出后清理空桶，避免被大量一次性键撑爆内存。
        self._max_keys = max_keys

    def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit <= 0:
            # limit<=0 视为"不限制"，便于用配置整体关掉某条限流
            return RateLimitDecision(allowed=True, remaining=0)

        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    self._evict_empty_locked()
                bucket = deque()
                self._buckets[key] = bucket

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                # 最早那次命中滑出窗口时即可重试
                retry_after = int(bucket[0] + window_seconds - now) + 1
                return RateLimitDecision(
                    allowed=False, remaining=0, retry_after=max(retry_after, 1)
                )

            bucket.append(now)
            return RateLimitDecision(allowed=True, remaining=limit - len(bucket))

    def _evict_empty_locked(self) -> int:
        empty = [key for key, bucket in self._buckets.items() if not bucket]
        for key in empty:
            del self._buckets[key]
        return len(empty)

    def sweep(self, *, window_seconds: int) -> int:
        """清掉全部已过期的桶。挂进 janitor 定时任务。

        没有这个清理，长期运行会为每个曾经出现过的键永久保留一个空 deque。
        """
        cutoff = time.monotonic() - window_seconds
        with self._lock:
            stale = [
                key
                for key, bucket in self._buckets.items()
                if not bucket or bucket[-1] <= cutoff
            ]
            for key in stale:
                del self._buckets[key]
            return len(stale)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._buckets)


__all__ = ["InMemoryRateLimiter"]
