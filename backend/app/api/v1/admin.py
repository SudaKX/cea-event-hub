"""管理端接口。

**整层挂 `require_admin`**（router 级依赖），而不是逐个端点自己加守卫——后者
迟早会漏掉一个。需要用到管理员对象的地方再单独声明 `AdminUser` 参数。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
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
from app.core.enums import PlatformSwitchKey
from app.core.exceptions import BadRequest, NotFound
from app.core.text import normalize_event_id
from app.repositories import paginate
from app.repositories.users import UserRepository
from app.schemas.auth import (
    ResetTokenResponse,
    UserAdminEnvelope,
    UserAdminPublic,
    UserBulkRequest,
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
from app.schemas.invitations import (
    CreateInvitationRequest,
    InvitationEnvelope,
    InvitationListResponse,
    InvitationUsagePublic,
    PlatformSwitchRequest,
    PlatformSwitchesPublic,
    invitation_to_public,
)
from app.schemas.submissions import (
    BatchDeleteRequest,
    BatchReviewRequest,
    SubmissionEnvelope,
    SubmissionListResponse,
    SubmissionReviewRequest,
    file_to_public,
    submission_to_public,
)
from app.services.auth import AuthService
from app.services.content import ContentService
from app.services.events import EventService
from app.services.invitations import CodeView, InvitationService
from app.services.submissions import SubmissionService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _submission_service(settings, storage) -> SubmissionService:
    return SubmissionService(settings, storage)


@dataclass(frozen=True)
class SubmitterView:
    """一个提交者在管理端该怎么显示。"""

    #: 比原始标识更可读的名字；**为 None 表示没有更好的名字**，界面回落到标识本身
    display: str | None
    #: 该标识指向的账号已经不在了
    deleted: bool


def _resolve_submitters(
    session, submitters: Iterable[str]
) -> dict[str, SubmitterView]:
    """把一批提交者**一次**查清楚：显示名，以及账号是否已被删除。

    匿名的 `a:{client_id}` 原样返回、不算删除（它本来就没有账号）；`u:{id}` 则在账号
    还在时给出显示名，账号已删时**不给名字**（界面回落到 `u:{id}` 本身），只标记为
    已删除。

    **"已删除"是标记，不是文案。** 界面用标签表达它，正文里就只留那串标识 —— 既少
    一层重复，也让标识保持可用：它正是筛选参数要用的值。而"已删除"之所以必须是独立
    标志，是因为它既不能靠 `user_id` 推（那是 `ON DELETE SET NULL`，账号一删就空了），
    也不能靠显示名去匹配字符串。

    **一次查询解析整页**：逐条查就是 N+1，一页 50 条就是 50 次。
    """
    views: dict[str, SubmitterView] = {
        submitter: SubmitterView(display=submitter, deleted=False)
        for submitter in submitters
    }

    user_ids: list[int] = []
    for submitter in submitters:
        if submitter.startswith("u:"):
            try:
                user_ids.append(int(submitter[2:]))
            except ValueError:
                # 形态不对就原样显示，不猜
                continue

    if not user_ids:
        return views

    found = UserRepository().display_names_for(session, sorted(set(user_ids)))
    for user_id in user_ids:
        name = found.get(user_id)
        views[f"u:{user_id}"] = SubmitterView(
            # 账号没了就没什么名字可显示，交给界面回落 —— 标记会说明原因
            display=name,
            deleted=name is None,
        )
    return views


def _render_submission(
    session, submission, service: SubmissionService, *, view: SubmitterView | None = None
):
    """管理端视图：附件的类型由**字节嗅探**得出，不采信客户端声明。"""
    records = service.files.list_for_submission(session, submission.id)
    return submission_to_public(
        submission,
        files=[
            file_to_public(record, service.attachment_mime(record))
            for record in records
        ],
        submitter_display=view.display if view else None,
        submitter_deleted=view.deleted if view else False,
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
        max_per_submitter=payload.max_per_submitter,
        visibility=payload.visibility,
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
        # 回显也取规范形态：地址栏里可以写大写，但响应里的标识只有一种形态，
        # 调用方（管理台）因此不必自己再归一化一次
        event_id=normalize_event_id(event_id),
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
        event_id=normalize_event_id(event_id),
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
    q: str | None = Query(default=None, description="在提交内容里做子串匹配"),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SubmissionListResponse:
    """按活动、分类标签、状态、提交者、时间范围与内容关键词筛选。"""
    service = _submission_service(settings, storage)
    statement = service.submissions.list_for_event(
        session,
        event_id=event_id,
        kind=kind,
        status=submission_status,
        submitter=submitter,
        created_from=created_from,
        created_to=created_to,
        payload_contains=q,
    )
    total = session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    rows = session.scalars(
        paginate(statement, offset=(page - 1) * page_size, limit=page_size)
    ).all()

    # **一次**解析整页的提交者，而不是每条查一次
    views = _resolve_submitters(session, {row.submitter for row in rows})

    return SubmissionListResponse(
        submissions=[
            _render_submission(
                session, row, service, view=views.get(row.submitter)
            )
            for row in rows
        ],
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
    # 审核后前端会用这条响应就地更新那一行，因此显示名也得带上 —— 否则刚审核完的
    # 那一行会从"张三"回落成 `u:7`
    views = _resolve_submitters(session, {submission.submitter})
    return SubmissionEnvelope(
        submission=_render_submission(
            session, submission, service, view=views.get(submission.submitter)
        )
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
    "/submissions:review",
    status_code=status.HTTP_200_OK,
    summary="批量改审核状态",
)
def review_submissions(
    payload: BatchReviewRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
    storage: FileStorageDep,
) -> dict[str, int]:
    """一次请求改一批状态。

    与 `:delete` 对称。存在的意义是省掉逐条往返：管理端会把跨页挑选的条目攒成一条
    队列再统一处理，逐条 PATCH 的话那就是几十个请求，而它们本该是一次事务。

    返回**实际改动的条数**：勾选期间被别处删掉的那些会被跳过，调用方据此知道
    结果与预期是否一致。
    """
    reviewed = _submission_service(settings, storage).review_many(
        session,
        submission_ids=payload.ids,
        status_value=payload.status,
        actor=admin,
    )
    return {"reviewed": reviewed}


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


@router.post(
    "/users:bulk",
    status_code=status.HTTP_200_OK,
    summary="批量改用户角色或启用状态",
)
def bulk_update_users(
    payload: UserBulkRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> dict[str, int]:
    """一次请求改一批用户的角色与／或启用状态。

    与提交那边的 `:review` 对称，理由相同：管理端会把名单攒起来统一处理，逐条
    `PATCH` 就是几十个请求，而它们本该是一次事务。

    返回**实际改动的条数**：期间被删掉的账号会被跳过。
    """
    updated = AuthService(settings).bulk_update(
        session,
        actor=admin,
        target_ids=payload.ids,
        role=payload.role,
        is_active=payload.is_active,
    )
    return {"updated": updated}


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


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除用户账号",
)
def delete_user(
    user_id: int,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> Response:
    """彻底删除账号。

    **与"停用"回答的是不同的问题**：停用是"这个人不在了"（可逆、提交署名完好），
    删除是"这个账号本就不该存在"（不可逆、署名此后只剩一个编号）。因此两者是并列的
    动作，不设"必须先停用"的门槛 —— 那挡不住误操作，真正挡住它的是确认框。

    **他的提交不会消失**，改的只是"谁交的"从此不可考。
    """
    AuthService(settings).delete_user(session, actor=admin, target_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/{user_id}/reset-token",
    response_model=ResetTokenResponse,
    summary="为用户签发一次性密码重置令牌",
)
def issue_reset_token(
    user_id: int,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> ResetTokenResponse:
    """邮件不可用时的密码找回路径：管理员签发，线下转交。

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


# ---------------------------------------------------------------------------
# 邀请码与平台开关
# ---------------------------------------------------------------------------


@router.get(
    "/invitations",
    response_model=InvitationListResponse,
    summary="全部邀请码",
)
def list_invitations(
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> InvitationListResponse:
    """全部邀请码，含每张的使用情况。

    与用户端看到的字段相同 —— 两张界面要显示的东西本来就一样（token、名称、有效期、
    次数、被谁用过）。区别只在**范围**：这里是全部，那里只有自己名下的。
    """
    views = InvitationService().list_all(session)
    return InvitationListResponse(
        invitations=[invitation_to_public(view.code, usages=[InvitationUsagePublic(username=u.username, used_at=u.used_at) for u in view.usages]) for view in views]
    )


@router.post(
    "/invitations",
    response_model=InvitationEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="创建邀请码",
)
def create_invitation(
    payload: CreateInvitationRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> InvitationEnvelope:
    """管理员建码，四项都可指定。

    **不受普通用户那两条额度限制**（同时一张未使用、每 24 小时一张）：那是为个人
    分享设计的，而这是运营动作。码的 `owner_id` 为空 —— 平台码，不归任何个人
    （design.md 决策 8）。
    """
    code = InvitationService().create(
        session,
        actor=admin,
        token=payload.token,
        name=payload.name,
        days=payload.days,
        max_uses=payload.max_uses,
    )
    logger.info(
        "管理员 %r 创建了邀请码 %r（%s 天、%s 次）",
        admin.username,
        code.token,
        payload.days,
        payload.max_uses,
    )
    return InvitationEnvelope(invitation=invitation_to_public(code))


@router.post(
    "/invitations/{code_id}/revoke",
    response_model=InvitationEnvelope,
    summary="使邀请码失效",
)
def revoke_invitation(
    code_id: int,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> InvitationEnvelope:
    """让一张码失效。

    **失效而不是删除**：已经产生使用记录的码必须留住那些记录 —— 删了它，"谁邀请了他"
    就无从回答（design.md 决策 9）。
    """
    code = InvitationService().revoke(session, actor=admin, code_id=code_id)
    logger.info("管理员 %r 使邀请码 %r 失效", admin.username, code.token)
    return InvitationEnvelope(invitation=invitation_to_public(code))


@router.get(
    "/switches",
    response_model=PlatformSwitchesPublic,
    summary="平台开关的当前状态",
)
def read_switches(session: DbSession, admin: AdminUser) -> PlatformSwitchesPublic:
    """两个应急开关。它们存在库里，因此改完**立即生效**，不需要重启进程。"""
    return PlatformSwitchesPublic(**InvitationService().switch_states(session))


@router.put(
    "/switches",
    response_model=PlatformSwitchesPublic,
    summary="改变平台开关",
)
def write_switch(
    payload: PlatformSwitchRequest,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> PlatformSwitchesPublic:
    """改一个开关。

    **暂停邀请**拒绝一切邀请码校验，含此前发出、仍在有效期内的码 —— 它是应急刹车，
    只挡新码等于没刹车。**暂停申请**只挡新申请，已发出的码照常可用。两者对应两类
    事故（码被泄露 / 某个账号在刷码），合成一个就总有一种场景要迁就另一种。
    """
    try:
        key = PlatformSwitchKey(payload.key)
    except ValueError:
        raise BadRequest("未知的开关") from None

    service = InvitationService()
    service.set_switch(session, actor=admin, key=key, enabled=payload.enabled)
    logger.info(
        "管理员 %r 把开关 %s 设为 %s", admin.username, key.value, payload.enabled
    )
    return PlatformSwitchesPublic(**service.switch_states(session))


__all__ = ["router"]
