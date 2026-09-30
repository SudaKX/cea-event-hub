"""活动相关的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Event


class QuotaState(BaseModel):
    """配额状态。

    `limit` 为 None 表示不限额，此时 `remaining` 也是 None——前端据此显示
    "不限名额"而不是"剩余 0"。
    """

    limit: int | None
    used: int
    remaining: int | None


class EventPublic(BaseModel):
    """公开活动信息。draft / archived 不出现在公开接口。"""

    id: str
    title: str
    summary: str | None = None
    status: str
    content_version: int
    entry_path: str
    submission_requires_login: bool
    submissions_open_at: datetime | None = None
    submissions_close_at: datetime | None = None
    quota: QuotaState


class EventAdmin(EventPublic):
    """管理端视图：额外暴露配额覆盖值与所有者。"""

    max_submissions: int | None = None
    owner_id: int | None = None
    created_at: datetime
    updated_at: datetime


class EventCreateRequest(BaseModel):
    # extra="forbid"：拼错字段名会被明确拒绝，而不是静默忽略后让人困惑
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    entry_path: str | None = Field(default=None, max_length=255)
    submission_requires_login: bool | None = None
    submissions_open_at: datetime | None = None
    submissions_close_at: datetime | None = None
    max_submissions: int | None = Field(default=None, ge=0)


class EventUpdateRequest(BaseModel):
    """部分更新。

    **刻意不含 `id`。** 配合 `extra="forbid"`，客户端一旦试图改标识就会拿到
    422——活动标识同时是 URL、内容目录名与数据目录名，改它等于撕裂目录。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = None
    status: str | None = None
    entry_path: str | None = Field(default=None, max_length=255)
    submission_requires_login: bool | None = None
    submissions_open_at: datetime | None = None
    submissions_close_at: datetime | None = None
    max_submissions: int | None = Field(default=None, ge=0)
    theme: dict[str, Any] | None = None


class EventEnvelope(BaseModel):
    event: EventPublic


class EventAdminEnvelope(BaseModel):
    event: EventAdmin


class EventListResponse(BaseModel):
    events: list[EventPublic]


class EventAdminListResponse(BaseModel):
    events: list[EventAdmin]


def to_public(event: Event, quota: QuotaState) -> EventPublic:
    return EventPublic(
        id=event.id,
        title=event.title,
        summary=event.summary,
        status=event.status,
        content_version=event.content_version,
        entry_path=event.entry_path,
        submission_requires_login=event.submission_requires_login,
        submissions_open_at=event.submissions_open_at,
        submissions_close_at=event.submissions_close_at,
        quota=quota,
    )


def to_admin(event: Event, quota: QuotaState) -> EventAdmin:
    return EventAdmin(
        **to_public(event, quota).model_dump(),
        max_submissions=event.max_submissions,
        owner_id=event.owner_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


__all__ = [
    "EventAdmin",
    "EventAdminEnvelope",
    "EventAdminListResponse",
    "EventCreateRequest",
    "EventEnvelope",
    "EventListResponse",
    "EventPublic",
    "EventUpdateRequest",
    "QuotaState",
    "to_admin",
    "to_public",
]
