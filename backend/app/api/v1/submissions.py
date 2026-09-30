"""提交接收接口。

两个端点里**文件端点是超集**：需要"字段 + 文件"的活动页只发一次 `:files`，
从而保证"一次提交 = 一行父行"。否则同一次提交会在管理端显示成两条记录。

端点刻意声明为同步（`def`）而不是 `async def`：数据库会话是同步的，在事件循环
里跑阻塞 IO 会拖住整个进程。FastAPI 会把同步端点放进线程池。
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentEvent,
    CurrentUser,
    DbSession,
    FileStorageDep,
    RateLimiterDep,
    RequiredUser,
    RuntimeSettings,
    limit_by_client_ip,
    limit_by_user,
    reject_oversized_request,
    request_client_ip,
    require_json_content_type,
)
from app.core.exceptions import ValidationFailed
from app.db.models import Submission
from app.repositories.submissions import SubmissionFileRepository
from app.schemas.submissions import (
    SubmissionCreated,
    SubmissionListResponse,
    file_to_public,
    submission_to_public,
)
from app.services.submissions import SubmissionService, Upload

router = APIRouter(prefix="/events", tags=["submissions"])
me_router = APIRouter(prefix="/me", tags=["submissions"])


def _service(settings, storage) -> SubmissionService:
    return SubmissionService(settings, storage)


def _guard_submit_rate(
    request: Request, limiter, *, user_id: int | None, event_id: str
) -> None:
    """提交端点限流：来源维度 + 用户维度。

    只查用户维度，换个账号就能绕过；只查来源维度，同一出口后的所有人会被连坐。
    """
    runtime = request.app.state.settings
    limit_by_client_ip(
        request,
        limiter,
        scope=f"submit:{event_id}",
        limit=runtime.RATE_LIMIT_SUBMIT_IP_MAX,
        window_seconds=runtime.RATE_LIMIT_SUBMIT_IP_WINDOW,
    )
    if user_id is not None:
        limit_by_user(
            limiter,
            scope=f"submit:{event_id}",
            user_id=user_id,
            limit=runtime.RATE_LIMIT_SUBMIT_USER_MAX,
            window_seconds=runtime.RATE_LIMIT_SUBMIT_USER_WINDOW,
        )


def _render(session: Session, submission: Submission) -> Any:
    records = SubmissionFileRepository().list_for_submission(session, submission.id)
    return submission_to_public(
        submission, files=[file_to_public(record) for record in records]
    )


def _parse_payload_part(raw: str | None) -> dict[str, Any]:
    """解析 multipart 里名为 `payload` 的文本部分。"""
    if raw is None or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed(fields={"payload": "payload 不是合法 JSON"}) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed(fields={"payload": "payload 必须是 JSON 对象"})
    return parsed


@router.post(
    "/{event_id}/submissions",
    status_code=status.HTTP_201_CREATED,
    response_model=SubmissionCreated,
    summary="提交信息（JSON）",
    dependencies=[
        Depends(require_json_content_type),
        Depends(reject_oversized_request),
    ],
)
def submit_json(
    payload: dict[str, Any],
    request: Request,
    event: CurrentEvent,
    session: DbSession,
    user: CurrentUser,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
    storage: FileStorageDep,
    kind: Annotated[str | None, Query()] = None,
    client_id: Annotated[str | None, Query()] = None,
) -> SubmissionCreated:
    """请求体**本身就是**提交内容，没有外层信封。

    `kind` 与匿名标识走查询串：前者是分组元信息，后者由宿主附加（不采信
    iframe 传值，但服务端同样不把它当凭据）。

    `kind` 刻意不设长度上限：不合规的值应当**回落到默认标签**而不是让整次提交
    失败，长度校验由消毒函数负责。
    """
    _guard_submit_rate(
        request, limiter, user_id=user.id if user else None, event_id=event.id
    )

    result = _service(settings, storage).submit(
        session,
        event=event,
        user=user,
        client_id=client_id,
        payload=payload,
        kind=kind,
        idem_key=request.headers.get("Idempotency-Key"),
        ip=request_client_ip(request),
    )
    return SubmissionCreated(
        submission=_render(session, result.submission),
        deduplicated=result.deduplicated,
    )


@router.post(
    "/{event_id}/submissions:files",
    status_code=status.HTTP_201_CREATED,
    response_model=SubmissionCreated,
    summary="提交信息与文件（multipart，JSON 端点的超集）",
    dependencies=[Depends(reject_oversized_request)],
)
def submit_files(
    request: Request,
    event: CurrentEvent,
    session: DbSession,
    user: CurrentUser,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
    storage: FileStorageDep,
    files: Annotated[list[UploadFile] | None, File()] = None,
    payload_part: Annotated[str | None, File(alias="payload")] = None,
    kind: Annotated[str | None, Query()] = None,
    client_id: Annotated[str | None, Query()] = None,
) -> SubmissionCreated:
    """字段放在名为 `payload` 的文本部分，文件放在可重复的 `files` 部分。"""
    _guard_submit_rate(
        request, limiter, user_id=user.id if user else None, event_id=event.id
    )

    uploads = [
        Upload(
            filename=upload.filename or "unnamed",
            stream=upload.file,
            declared_mime=upload.content_type,
        )
        for upload in (files or [])
    ]

    result = _service(settings, storage).submit(
        session,
        event=event,
        user=user,
        client_id=client_id,
        payload=_parse_payload_part(payload_part),
        kind=kind,
        uploads=uploads,
        idem_key=request.headers.get("Idempotency-Key"),
        ip=request_client_ip(request),
    )
    return SubmissionCreated(
        submission=_render(session, result.submission),
        deduplicated=result.deduplicated,
    )


@me_router.get("/submissions", response_model=SubmissionListResponse, summary="我的提交")
def my_submissions(
    session: DbSession,
    user: RequiredUser,
    settings: RuntimeSettings,
    storage: FileStorageDep,
    event_id: Annotated[str | None, Query(max_length=64)] = None,
) -> SubmissionListResponse:
    """匿名用户没有这个接口。

    匿名标识不是凭据，靠它认人等于"猜到一个 id 就能读别人的提交记录"。
    """
    service = _service(settings, storage)
    submissions = service.list_for_user(session, user=user, event_id=event_id)
    return SubmissionListResponse(
        submissions=[_render(session, item) for item in submissions]
    )


__all__ = ["me_router", "router"]
