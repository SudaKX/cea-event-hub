"""活动。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.core.enums import EventStatus
from app.db.base import Base
from app.db.types import UtcDateTime


class Event(Base):
    __tablename__ = "events"

    # 不可变、URL 安全的字符串主键。它同时是 SPA 路径、内容目录名与数据目录名
    # 所共用的那个值，因此省掉了 slug <-> id 映射，也不会因重命名撕裂目录
    # （design.md 决策 15）。
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EventStatus.DRAFT.value, index=True
    )

    # 活动内容入口页，相对于 /content/{event_id}/
    entry_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="index.html"
    )
    # 每次重新投放内容包递增，宿主据此拼 ?v= 破缓存
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    theme: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # ---- 提交策略（活动级；表单契约已移除，故不再有逐表单配置）----
    submission_requires_login: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    submissions_open_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    submissions_close_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    # 覆盖默认条数上限；为空时按 submission_requires_login 取全局默认
    max_submissions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 配额计数器：由单语句 CAS 维护（design.md 决策 9）。
    # 单条删除必须在同一事务内递减，否则管理员失去"删一条腾一个名额"这条后路。
    submission_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event id={self.id!r} status={self.status}>"
