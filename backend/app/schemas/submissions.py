"""提交相关的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

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
    #: 审核状态码。取值与含义见 core/enums.py 的 SubmissionStatus
    status: int
    submitter: str
    #: 比 `submitter` 更可读的显示名。管理端在账号**还在**时给显示名；**为 None 表示
    #: 没有更好的名字**（匿名的 `a:{id}`、账号已删除的 `u:{id}`），界面回落到
    #: `submitter` 本身 —— 那串标识仍然有用，它就是筛选参数要用的值
    submitter_display: str | None = None
    #: 是否来自登录用户。管理端据此区分匿名提交，而不必去解析 submitter 前缀
    from_authenticated_user: bool
    #: 该提交来自登录用户，**但那个账号已被删除**。
    #:
    #: 与"匿名"是两回事，而它们一度被混为一谈：判据曾经是 `user_id is not None`，
    #: 而 `submissions.user_id` 是 `ON DELETE SET NULL` —— 账号一删，那条提交就
    #: 长出了「匿名」标记，好像它是访客交的。判据因此改成 `submitter` 的前缀：
    #: 那个字符串是刻意保留的（决策 4），不随账号删除而变。
    submitter_deleted: bool = False
    payload: dict[str, Any]
    created_at: datetime
    files: list[SubmissionFilePublic] = []


class SubmissionEnvelope(BaseModel):
    submission: SubmissionPublic


class SubmissionCreated(BaseModel):
    """创建结果。

    `deduplicated` 让客户端能区分"新建了一条"与"这是你刚才那次提交"——
    两者对用户的意义完全不同（前者该提示成功，后者不该重复提示）。
    """

    submission: SubmissionPublic
    deduplicated: bool


class SubmissionListResponse(BaseModel):
    submissions: list[SubmissionPublic]
    #: 管理端分页时给出总数；"我的提交"不翻页，保持为 0
    total: int = 0


class SubmissionReviewRequest(BaseModel):
    """改审核状态。取值必须是 `SubmissionStatus` 里的码值之一。

    这里不写 `Literal`：码值是契约的一部分，而合法集合的定义在枚举里 —— 两处各写
    一份迟早会漂移。非法值由 service 统一拒绝，错误形状也与其他校验一致。
    """

    status: int


class BatchDeleteRequest(BaseModel):
    # 上限存在的意义是让一次请求的代价可预期，而不是安全边界
    ids: list[int] = Field(min_length=1, max_length=500)


class BatchReviewRequest(BaseModel):
    """批量改审核状态。

    码值的合法集合在 `SubmissionStatus` 里，由 service 统一拒绝非法值 ——
    这里不重复写一份，两处定义迟早漂移。
    """

    ids: list[int] = Field(min_length=1, max_length=500)
    status: int


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
    submitter_display: str | None = None,
    submitter_deleted: bool = False,
) -> SubmissionPublic:
    return SubmissionPublic(
        id=submission.id,
        event_id=submission.event_id,
        kind=submission.kind or "_default",
        status=submission.status,
        submitter=submission.submitter,
        submitter_display=submitter_display,
        # **判据是提交者标识的前缀，不是 user_id。** `submissions.user_id` 是
        # `ON DELETE SET NULL`：账号一删它就被清空，于是那条提交会被当成匿名 ——
        # 而 `submitter` 是刻意保留的（决策 4），不随账号删除而变
        from_authenticated_user=submission.submitter.startswith("u:"),
        submitter_deleted=submitter_deleted,
        payload=submission.payload or {},
        created_at=submission.created_at,
        files=files if files is not None else [],
    )


__all__ = [
    "BatchDeleteRequest",
    "BatchReviewRequest",
    "SubmissionCreated",
    "SubmissionEnvelope",
    "SubmissionFilePublic",
    "SubmissionListResponse",
    "SubmissionPublic",
    "SubmissionReviewRequest",
    "file_to_public",
    "submission_to_public",
]
