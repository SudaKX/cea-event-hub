"""提交接收接口。

两个端点里**文件端点是超集**：需要"字段 + 文件"的活动页只发一次 `:files`，
从而保证"一次提交 = 一行父行"。否则同一次提交会在管理端显示成两条记录。

端点刻意声明为同步（`def`）而不是 `async def`：数据库会话是同步的，在事件循环
里跑阻塞 IO 会拖住整个进程。FastAPI 会把同步端点放进线程池。
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.exceptions import NotFound
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


# ---------------------------------------------------------------------------
# 附件下载
# ---------------------------------------------------------------------------

download_router = APIRouter(prefix="/submissions", tags=["attachments"])

#: 只允许这些字符进入 Content-Disposition 的 ASCII 形式。
#: 引号与反斜杠能改写头部结构，换行能注入额外的头，控制字符同理。
_ASCII_SAFE = re.compile(r"[^A-Za-z0-9._\- ]")


def _content_disposition(original_name: str) -> str:
    """构造既能被旧客户端读懂、又不会被用来注入头部的下载文件名。

    非 ASCII 文件名走 RFC 5987 的 `filename*`；同时给一个降级用的 ASCII
    `filename`，把危险字符替换掉。
    """
    from urllib.parse import quote

    name = (original_name or "download").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable()) or "download"

    ascii_name = _ASCII_SAFE.sub("_", name)[:150] or "download"
    encoded = quote(name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


@download_router.get(
    "/{submission_id}/files/{file_id}",
    summary="下载附件（提交者本人或管理员）",
    response_class=StreamingResponse,
)
def download_attachment(
    submission_id: int,
    file_id: int,
    session: DbSession,
    user: RequiredUser,
    settings: RuntimeSettings,
    storage: FileStorageDep,
) -> StreamingResponse:
    """附件唯一的读取路径。

    `/data` 既不静态暴露也不挂载，因此这里是拿到字节的唯一入口。响应强制
    `attachment` + 通用二进制类型 + `nosniff`：这是"接受任意文件类型"能够成立
    的前提——上传的 `.html` 永远不会在浏览器里被当作页面执行。
    """
    service = _service(settings, storage)

    submission = service.submissions.get(session, submission_id)
    if submission is None:
        raise NotFound("附件不存在")

    # 非提交者且非管理员 -> 404，而不是 403：
    # 403 会确认"这个附件存在但不给你看"
    service.assert_can_read_files(submission, user)

    record = service.files.get(session, file_id)
    if record is None or record.submission_id != submission_id:
        raise NotFound("附件不存在")
    if not storage.exists(record.event_id, record.stored_rel):
        raise NotFound("附件不存在")

    handle = storage.open(record.event_id, record.stored_rel)
    return StreamingResponse(
        handle,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(record.original_name),
            "Content-Length": str(record.size_bytes),
            # 阻止浏览器自行嗅探类型后改变处理方式
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["download_router", "me_router", "router"]
