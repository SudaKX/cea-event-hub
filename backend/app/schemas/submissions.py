"""提交相关的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import Submission, SubmissionFile


class SubmissionFilePublic(BaseModel):
    id: int
    original_name: str
    size_bytes: int
    mime: str | None = None
    sha256: str
    created_at: datetime


class SubmissionPublic(BaseModel):
    id: int
    event_id: str
    kind: str
    status: str
    submitter: str
    #: 是否来自登录用户。管理端据此区分匿名提交，而不必去解析 submitter 前缀
    from_authenticated_user: bool
    payload: dict[str, Any]
    created_at: datetime
    files: list[SubmissionFilePublic] = []


class SubmissionCreated(BaseModel):
    """创建结果。

    `deduplicated` 让客户端能区分"新建了一条"与"这是你刚才那次提交"——
    两者对用户的意义完全不同（前者该提示成功，后者不该重复提示）。
    """

    submission: SubmissionPublic
    deduplicated: bool


class SubmissionListResponse(BaseModel):
    submissions: list[SubmissionPublic]


class SubmissionReviewRequest(BaseModel):
    status: str


def file_to_public(record: SubmissionFile, mime: str | None = None) -> SubmissionFilePublic:
    return SubmissionFilePublic(
        id=record.id,
        original_name=record.original_name,
        size_bytes=record.size_bytes,
        mime=mime or record.mime,
        sha256=record.sha256,
        created_at=record.created_at,
    )


def submission_to_public(
    submission: Submission,
    *,
    files: list[SubmissionFilePublic] | None = None,
) -> SubmissionPublic:
    return SubmissionPublic(
        id=submission.id,
        event_id=submission.event_id,
        kind=submission.kind or "_default",
        status=submission.status,
        submitter=submission.submitter,
        from_authenticated_user=submission.user_id is not None,
        payload=submission.payload or {},
        created_at=submission.created_at,
        files=files if files is not None else [],
    )


__all__ = [
    "SubmissionCreated",
    "SubmissionFilePublic",
    "SubmissionListResponse",
    "SubmissionPublic",
    "SubmissionReviewRequest",
    "file_to_public",
    "submission_to_public",
]
