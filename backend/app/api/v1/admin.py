"""管理端接口。

**整层挂 `require_admin`**（router 级依赖），而不是逐个端点自己加守卫——后者
迟早会漏掉一个。需要用到管理员对象的地方再单独声明 `AdminUser` 参数。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.core.deps import (
    AdminUser,
    DbSession,
    FileStorageDep,
    RuntimeSettings,
    require_admin,
)
from app.core.exceptions import NotFound
from app.repositories import paginate
from app.repositories.users import UserRepository
from app.schemas.auth import (
    ResetTokenResponse,
    UserAdminEnvelope,
    UserAdminPublic,
    UserListResponse,
    UserUpdateRequest,
)
from app.schemas.content import (
    ContentDeployResponse,
    ContentFileItem,
    ContentListResponse,
)
from app.schemas.events import (
    EventAdminEnvelope,
    EventAdminListResponse,
    EventCreateRequest,
    EventUpdateRequest,
    to_admin,
)
from app.schemas.submissions import (
    BatchDeleteRequest,
    SubmissionEnvelope,
    SubmissionListResponse,
    SubmissionReviewRequest,
    file_to_public,
    submission_to_public,
)
from app.services.auth import AuthService
from app.services.content import ContentService
from app.services.events import EventService
from app.services.submissions import SubmissionService

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _submission_service(settings, storage) -> SubmissionService:
    return SubmissionService(settings, storage)


def _render_submission(session, submission, service: SubmissionService):
    """管理端视图：附件的类型由**字节嗅探**得出，不采信客户端声明。"""
    records = service.files.list_for_submission(session, submission.id)
    return submission_to_public(
        submission,
        files=[
            file_to_public(record, service.attachment_mime(record))
            for record in records
        ],
    )


# ---------------------------------------------------------------------------
# 活动
# ---------------------------------------------------------------------------


@router.get("/events", response_model=EventAdminListResponse, summary="活动列表（含未发布）")
def list_events(
    session: DbSession,
    settings: RuntimeSettings,
    event_status: str | None = Query(default=None, alias="status"),
) -> EventAdminListResponse:
    service = EventService(settings)
    events = service.list_admin(session, status=event_status)
    return EventAdminListResponse(
        events=[to_admin(event, service.quota_state(event)) for event in events]
    )


@router.post(
    "/events",
    status_code=status.HTTP_201_CREATED,
    response_model=EventAdminEnvelope,
    summary="创建活动",
)
def create_event(
    payload: EventCreateRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> EventAdminEnvelope:
    service = EventService(settings)
    event = service.create(
        session,
        event_id=payload.id,
        title=payload.title,
        summary=payload.summary,
        entry_path=payload.entry_path,
        submission_requires_login=payload.submission_requires_login,
        submissions_open_at=payload.submissions_open_at,
        submissions_close_at=payload.submissions_close_at,
        max_submissions=payload.max_submissions,
        owner=admin,
    )
    # 新建活动一律为 draft，必须显式发布才会出现在公开接口
    return EventAdminEnvelope(event=to_admin(event, service.quota_state(event)))


@router.get(
    "/events/{event_id}", response_model=EventAdminEnvelope, summary="活动详情（含未发布）"
)
def get_event_admin(
    event_id: str, session: DbSession, settings: RuntimeSettings
) -> EventAdminEnvelope:
    service = EventService(settings)
    event = service.repo.get(session, event_id)
    if event is None:
        raise NotFound("活动不存在")
    return EventAdminEnvelope(event=to_admin(event, service.quota_state(event)))


@router.patch(
    "/events/{event_id}", response_model=EventAdminEnvelope, summary="修改活动"
)
def update_event(
    event_id: str,
    payload: EventUpdateRequest,
    session: DbSession,
    settings: RuntimeSettings,
) -> EventAdminEnvelope:
    service = EventService(settings)
    # exclude_unset 让"没传的字段"与"显式传 null"可区分
    event = service.update(
        session, event_id=event_id, changes=payload.model_dump(exclude_unset=True)
    )
    return EventAdminEnvelope(event=to_admin(event, service.quota_state(event)))


@router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除活动",
)
def delete_event(
    event_id: str, session: DbSession, response: Response, settings: RuntimeSettings
) -> Response:
    """物理删除并级联移除提交与附件记录。

    归档（PATCH status=archived）才是默认手段；这里是显式破坏性操作。
    """
    EventService(settings).delete(session, event_id=event_id)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------------------
# 活动内容
# ---------------------------------------------------------------------------


def _upload_size(upload: UploadFile) -> int:
    """取上传体积。

    Starlette 的 UploadFile 通常带 `size`，但为 None 时（例如手工构造的
    SpooledTemporaryFile）回退到量一次长度，避免体积上限被绕过。
    """
    size = getattr(upload, "size", None)
    if isinstance(size, int):
        return size
    stream = upload.file
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


@router.post(
    "/events/{event_id}/content",
    status_code=status.HTTP_201_CREATED,
    response_model=ContentDeployResponse,
    summary="投放活动内容包（zip）",
)
def deploy_content(
    event_id: str,
    session: DbSession,
    settings: RuntimeSettings,
    file: UploadFile = File(...),
) -> ContentDeployResponse:
    """整体替换该活动的内容目录，并递增 `content_version`。

    校验不通过时目标目录**完全不被触碰**，版本号也不递增——解压前已逐条校验
    条目名、符号链接、条目数与解压体积。
    """
    result = ContentService(settings).deploy_archive(
        session, event_id=event_id, stream=file.file, size_bytes=_upload_size(file)
    )
    return ContentDeployResponse(
        event_id=event_id,
        file_count=result.file_count,
        total_bytes=result.total_bytes,
        content_version=result.content_version,
    )


@router.get(
    "/events/{event_id}/content",
    response_model=ContentListResponse,
    summary="活动内容清单",
)
def list_content(
    event_id: str, session: DbSession, settings: RuntimeSettings
) -> ContentListResponse:
    service = ContentService(settings)
    event = EventService(settings).repo.get(session, event_id)
    if event is None:
        raise NotFound("活动不存在")

    return ContentListResponse(
        event_id=event_id,
        content_version=event.content_version,
        entry_path=event.entry_path,
        files=[
            ContentFileItem(path=item.path, size_bytes=item.size_bytes)
            for item in service.list_files(event_id)
        ],
    )


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 提交审核
# ---------------------------------------------------------------------------


@router.get(
    "/events/{event_id}/submissions",
    response_model=SubmissionListResponse,
    summary="活动的提交列表",
)
def list_submissions(
    event_id: str,
    session: DbSession,
    settings: RuntimeSettings,
    storage: FileStorageDep,
    kind: str | None = None,
    submission_status: int | None = Query(default=None, alias="status"),
    submitter: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SubmissionListResponse:
    """按活动、分类标签、状态、提交者与时间范围筛选。"""
    service = _submission_service(settings, storage)
    statement = service.submissions.list_for_event(
        session,
        event_id=event_id,
        kind=kind,
        status=submission_status,
        submitter=submitter,
        created_from=created_from,
        created_to=created_to,
    )
    total = session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    rows = session.scalars(
        paginate(statement, offset=(page - 1) * page_size, limit=page_size)
    ).all()

    return SubmissionListResponse(
        submissions=[_render_submission(session, row, service) for row in rows],
        total=total,
    )


@router.patch(
    "/submissions/{submission_id}",
    response_model=SubmissionEnvelope,
    summary="审核提交（变更状态）",
)
def review_submission(
    submission_id: int,
    payload: SubmissionReviewRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
    storage: FileStorageDep,
) -> SubmissionEnvelope:
    service = _submission_service(settings, storage)
    submission = service.review(
        session, submission_id=submission_id, status_value=payload.status, actor=admin
    )
    return SubmissionEnvelope(
        submission=_render_submission(session, submission, service)
    )


@router.delete(
    "/submissions/{submission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除提交（并释放名额）",
)
def delete_submission(
    submission_id: int,
    session: DbSession,
    response: Response,
    settings: RuntimeSettings,
    storage: FileStorageDep,
) -> Response:
    _submission_service(settings, storage).delete_submission(
        session, submission_id=submission_id
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/submissions:delete",
    status_code=status.HTTP_200_OK,
    summary="批量删除提交",
)
def delete_submissions(
    payload: BatchDeleteRequest,
    session: DbSession,
    settings: RuntimeSettings,
    storage: FileStorageDep,
) -> dict[str, int]:
    """批量删除后按实际行数重算计数。

    逐条递减在批量场景下容易漏，而重算顺便成为计数器唯一需要的自愈入口。
    """
    deleted = _submission_service(settings, storage).delete_many(
        session, submission_ids=payload.ids
    )
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserListResponse, summary="用户列表")
def list_users(
    session: DbSession,
    settings: RuntimeSettings,
    role: str | None = None,
    is_active: bool | None = None,
    username: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UserListResponse:
    repo = UserRepository()
    statement = repo.list_users(
        session, role=role, is_active=is_active, username_like=username
    )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(
        paginate(statement, offset=(page - 1) * page_size, limit=page_size)
    ).all()
    return UserListResponse(
        users=[UserAdminPublic.from_model(row) for row in rows], total=total
    )


@router.patch(
    "/users/{user_id}", response_model=UserAdminEnvelope, summary="修改用户角色或状态"
)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> UserAdminEnvelope:
    """提权、降权、停用与启用。

    角色与状态变更都会吊销该用户的全部会话：不吊销的话，降级后的用户在旧会话里
    仍然持有管理权限，而停用也只是"下次登录才生效"。
    """
    service = AuthService(settings)
    changes = payload.model_dump(exclude_unset=True)

    target = None
    if "role" in changes and changes["role"] is not None:
        target = service.set_role(
            session, actor=admin, target_id=user_id, role=changes["role"]
        )
    if "is_active" in changes and changes["is_active"] is not None:
        target = service.set_active(
            session, actor=admin, target_id=user_id, is_active=changes["is_active"]
        )
    if "display_name" in changes and changes["display_name"] is not None:
        target = service.users.get(session, user_id)
        if target is None:
            raise NotFound("用户不存在")
        target.display_name = changes["display_name"]

    if target is None:
        target = service.users.get(session, user_id)
        if target is None:
            raise NotFound("用户不存在")

    return UserAdminEnvelope(user=UserAdminPublic.from_model(target))


@router.post(
    "/users/{user_id}/reset-token",
    response_model=ResetTokenResponse,
    summary="为用户签发一次性口令重置令牌",
)
def issue_reset_token(
    user_id: int,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> ResetTokenResponse:
    """邮件不可用时的口令找回路径：管理员签发，线下转交。

    明文只在这个响应里出现一次。此后任何查询接口都不会返回它——库里存的是摘要。
    """
    target, token, expires_at = AuthService(settings).admin_issue_reset_token(
        session, actor=admin, target_id=user_id
    )
    return ResetTokenResponse(
        user_id=target.id,
        username=target.username,
        token=token,
        expires_at=expires_at,
    )


__all__ = ["router"]
