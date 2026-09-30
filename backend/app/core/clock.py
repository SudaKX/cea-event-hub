"""时间工具。

全库统一 UTC 存储（可移植性规则第 5 条）：SQLite 没有时区感知类型，混用
aware/naive 会让比较直接抛错。因此所有时间戳都从这里取，且一律带时区。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """当前 UTC 时间（时区感知）。"""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """把 naive 时间视为 UTC 并补齐时区，避免与 aware 时间比较时抛错。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
