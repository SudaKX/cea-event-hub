"""自定义列类型：把跨库差异收在一处。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """统一 UTC 的 DateTime。

    SQLite 与 MySQL 的 DATETIME **都不带时区**：SQLAlchemy 的 SQLite 方言把
    aware 时间当字符串存下，读回来却是 naive 的。应用层一旦混用 aware/naive，
    比较会直接抛 TypeError，而且两个库上的表现还不一致——属于最难查的一类
    缺陷（可移植性规则第 5 条）。

    这个类型把边界收死：

    * 写入：aware -> 转成 UTC 后去掉 tzinfo（两库都存 UTC 朴素时间）
    * 读取：补回 UTC tzinfo（应用层永远只看到 aware UTC）

    因此"库里是 UTC"与"应用层是 aware UTC"这两条同时成立。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UtcDateTime 只接受 datetime，收到 {type(value)!r}")
        if value.tzinfo is None:
            # naive 视为已是 UTC：应用层的时间一律来自 core.clock.utcnow()
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
