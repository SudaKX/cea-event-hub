"""提交接收的业务规则。

这是整个系统的核心路径。规格里那串"先解析活动、再限流、再去重、最后落盘落库"
的顺序在这里落地，其中**两条顺序是刻意且不显然的**：

1. **去重在配额之前。** 网络抖动导致的重试应当被识别成"返回原提交"，它没有
   产生新行，就不该消耗名额。顺序反了的话，一个反复重试的客户端会在自己已经
   被去重的情况下把配额撞满。

2. **先写字节，再落库。** 数据库提交与文件落盘无法组成一个事务，因此约定：
   字节先落盘 -> 再提交数据库 -> 数据库失败则删字节。崩溃留下的 `pending`
   记录由 janitor 回收。

配额占用、插入与文件元信息都在**同一个请求事务**里，因此"插入失败时名额自动
回退"不需要额外代码——整体回滚即可。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, BinaryIO, Sequence

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.enums import EventStatus, StorageState, SubmissionStatus
from app.core.exceptions import (
    EventClosed,
    Forbidden,
    LoginRequired,
    PayloadTooLarge,
    QuotaExhausted,
    ValidationFailed,
)
from app.core.ports import FileStorage
from app.core.security import canonical_json, hash_ip
from app.core.text import sanitize_kind, truncate
from app.db.models import Event, Submission, SubmissionFile, User
from app.infra.storage_local import sniff_mime
from app.repositories.events import EventRepository
from app.repositories.submissions import (
    SubmissionFileRepository,
    SubmissionRepository,
)
from app.services.events import EventService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Upload:
    """一个待落盘的上传。"""

    filename: str
    stream: BinaryIO
    declared_mime: str | None = None


@dataclass(frozen=True)
class SubmissionResult:
    submission: Submission
    deduplicated: bool


class SubmissionService:
    def __init__(
        self,
        settings: Settings,
        storage: FileStorage,
        *,
        event_repo: EventRepository | None = None,
        submission_repo: SubmissionRepository | None = None,
        file_repo: SubmissionFileRepository | None = None,
        event_service: EventService | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.events = event_repo or EventRepository()
        self.submissions = submission_repo or SubmissionRepository()
        self.files = file_repo or SubmissionFileRepository()
        self.event_service = event_service or EventService(settings, self.events)

    # ------------------------------------------------------------------
    # 前置校验
    # ------------------------------------------------------------------

    def ensure_accepting(self, event: Event, user: User | None) -> None:
        """活动状态、提交窗口与登录要求。

        四种"不能提交"的原因刻意使用不同状态码，客户端才能判断值得重试还是
        到此为止（design.md 决策 9）。
        """
        if event.status != EventStatus.LIVE.value:
            raise EventClosed("活动未发布或已归档")

        reason = self.event_service.submission_window_error(event)
        if reason is not None:
            raise EventClosed()

        if event.submission_requires_login and user is None:
            raise LoginRequired()

    def resolve_submitter(self, user: User | None, client_id: str | None) -> str:
        """统一的提交者标识。

        登录时由**服务端会话**推导，客户端提供的值无法覆盖——否则任何客户端
        都能把自己伪装成别人。匿名的客户端标识可伪造，因此它只作分组用途，
        绝不参与鉴权（design.md 决策 7）。
        """
        if user is not None:
            return f"u:{user.id}"

        cleaned = (client_id or "").strip()
        if not cleaned:
            # 匿名提交必须带客户端标识。
            #
            # 少了它，同一个活动下**所有**匿名提交都会塌缩成 `a:unknown` 这一个
            # 提交者：管理端无法区分它们，按提交者做的分组与统计全部失真。
            # SDK 由宿主持久化并提供 clientId，所以走 SDK 的活动页永远不会缺它；
            # 直连 API 的调用方则会在这里立刻知道。
            raise ValidationFailed(
                "匿名提交必须带 client_id",
                fields={"client_id": "缺少客户端标识"},
            )

        return f"a:{truncate(cleaned, 64)}"

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------

    def submit(
        self,
        session: Session,
        *,
        event: Event,
        user: User | None,
        client_id: str | None,
        payload: dict[str, Any],
        kind: str | None = None,
        uploads: Sequence[Upload] = (),
        idem_key: str | None = None,
        ip: str = "",
    ) -> SubmissionResult:
        self.ensure_accepting(event, user)
        self._validate_payload(payload)
        self._validate_uploads(uploads)

        submitter = self.resolve_submitter(user, client_id)
        safe_kind = sanitize_kind(kind)

        # ---- 幂等（必须在配额之前）----
        #
        # 只认客户端给的幂等键。**刻意不再按内容哈希去重**：那种启发式不看请求
        # 身份、只看内容相似度，误判时会把用户的提交连同附件一起静默丢弃 ——
        # 而"字段没改、只换了一个附件"是真实会发生的场景。
        # 重复提交留下两条记录是响亮且可恢复的（管理员看得见、删掉即释放名额），
        # 静默丢弃则是安静且不可恢复的。两害相权取其轻。
        #
        # 防连点由活动页禁用按钮承担（示例页已如此），防重传由幂等键承担。
        if idem_key:
            existing = self.submissions.find_by_idem_key(
                session, event_id=event.id, idem_key=truncate(idem_key, 64)
            )
            if existing is not None:
                return SubmissionResult(existing, deduplicated=True)

        # ---- 配额（单语句 CAS，与后面的插入同事务）----
        self._acquire_quota(session, event)

        # 落盘与落库无法组成一个事务，因此：先写字节，再落库，
        # **任何后续失败都要把已写的字节删掉**。只在"写文件那一步失败"时清理
        # 是不够的——数据库提交失败同样会留下孤儿文件。
        stored: list[tuple[Upload, Any]] = []
        try:
            stored = self._store_files(event.id, safe_kind, uploads)

            submission = Submission(
                event_id=event.id,
                submitter=submitter,
                user_id=user.id if user else None,
                client_id=None if user else (client_id or None),
                ip_hash=self._hash_ip(ip),
                kind=safe_kind,
                payload=payload,
                idem_key=truncate(idem_key, 64) if idem_key else None,
                status=SubmissionStatus.RECEIVED.value,
            )
            self.submissions.add(session, submission)

            for upload, stored_file in stored:
                self.files.add(
                    session,
                    SubmissionFile(
                        submission_id=submission.id,
                        event_id=event.id,
                        stored_rel=stored_file.stored_rel,
                        original_name=truncate(upload.filename or "unnamed", 255),
                        ext=_extension(stored_file.stored_rel),
                        mime=upload.declared_mime,
                        size_bytes=stored_file.size_bytes,
                        sha256=stored_file.sha256,
                        # 落库成功后才置为 committed；崩溃留下的 pending 由
                        # janitor 回收
                        storage_state=StorageState.PENDING.value,
                    ),
                )
            session.flush()

            for record in submission.files:
                record.storage_state = StorageState.COMMITTED.value
        except Exception:
            for _, stored_file in stored:
                self.storage.delete(event.id, stored_file.stored_rel)
            # 数据库那侧（含配额占用）由请求事务整体回滚，无需手工回退
            raise

        return SubmissionResult(submission, deduplicated=False)

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    def _validate_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ValidationFailed(fields={"payload": "提交内容必须是 JSON 对象"})
        serialized = canonical_json(payload).encode("utf-8")
        if len(serialized) > self.settings.MAX_PAYLOAD_BYTES:
            raise PayloadTooLarge(
                f"提交内容超过上限（{self.settings.MAX_PAYLOAD_BYTES} 字节）"
            )

    def _validate_uploads(self, uploads: Sequence[Upload]) -> None:
        if not uploads:
            return
        if len(uploads) > self.settings.MAX_FILES_PER_REQUEST:
            raise ValidationFailed(
                fields={"files": f"单次最多上传 {self.settings.MAX_FILES_PER_REQUEST} 个文件"}
            )

    def _acquire_quota(self, session: Session, event: Event) -> None:
        limit = self.event_service.effective_quota_limit(event)
        if limit is None:
            # 不限额时仍然计数，供管理端展示已收条数
            self.events.increment_submission_count(session, event.id)
            return
        if not self.events.try_acquire_submission_slot(session, event.id, limit=limit):
            raise QuotaExhausted()

    def _store_files(
        self, event_id: str, kind: str, uploads: Sequence[Upload]
    ) -> list[tuple[Upload, Any]]:
        stored: list[tuple[Upload, Any]] = []
        try:
            for upload in uploads:
                result = self.storage.save(
                    event_id,
                    kind=kind,
                    original_name=upload.filename or "",
                    stream=upload.stream,
                    max_bytes=self.settings.MAX_UPLOAD_BYTES,
                )
                stored.append((upload, result))
        except Exception:
            # 写文件这一步自身失败：清掉本次已写下的部分再抛出。
            # 调用方的 try 块负责覆盖"后续落库失败"的情形。
            for _, result in stored:
                self.storage.delete(event_id, result.stored_rel)
            raise
        return stored

    def _hash_ip(self, ip: str) -> str | None:
        if not ip:
            return None
        return hash_ip(ip, self.settings.IP_HASH_SALT)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def list_for_event(self, session: Session, *, event_id: str, **filters) -> list[Submission]:
        return list(
            session.scalars(
                self.submissions.list_for_event(session, event_id=event_id, **filters)
            ).all()
        )

    def list_for_user(
        self, session: Session, *, user: User, event_id: str | None = None
    ) -> list[Submission]:
        """匿名用户没有提交历史：读取历史必须基于已登录会话。

        匿名标识不是凭据，靠它认人等于"猜到一个 id 就能读别人的提交"。
        """
        return list(
            session.scalars(
                self.submissions.list_for_submitter(
                    session, submitter=f"u:{user.id}", event_id=event_id
                )
            ).all()
        )

    def get_owned(
        self, session: Session, *, submission_id: int, user: User
    ) -> Submission:
        """取一条属于该用户的提交；否则 404（不泄露存在性）。"""
        from app.core.exceptions import NotFound

        submission = self.submissions.get(session, submission_id)
        if submission is None or submission.user_id != user.id:
            raise NotFound("提交不存在")
        return submission

    def assert_can_read_files(self, submission: Submission, user: User | None) -> None:
        """附件访问判定：提交者本人或管理员，其他人一律 404。

        用 404 而不是 403：403 会确认"这个附件存在但不给你看"。
        """
        from app.core.enums import UserRole
        from app.core.exceptions import NotFound

        if user is None:
            raise LoginRequired()
        if user.role == UserRole.ADMIN.value:
            return
        if submission.user_id == user.id:
            return
        raise NotFound("附件不存在")

    # ------------------------------------------------------------------
    # 审核与清理（管理端）
    # ------------------------------------------------------------------

    def review(
        self,
        session: Session,
        *,
        submission_id: int,
        status_value: int,
        actor: User,
    ) -> Submission:
        from app.core.exceptions import NotFound

        if status_value not in {s.value for s in SubmissionStatus}:
            raise ValidationFailed(fields={"status": "状态取值不合法"})

        submission = self.submissions.get(session, submission_id)
        if submission is None:
            raise NotFound("提交不存在")

        submission.status = status_value
        submission.reviewed_at = utcnow()
        submission.reviewed_by = actor.id
        return submission

    def delete_submission(self, session: Session, *, submission_id: int) -> Submission:
        """删除一条提交，并在**同一事务内**释放它占用的名额。

        递减不能省：满额活动上"删一条腾一个名额"是管理员唯一的自救手段，
        不递减会让活动被永久锁死且无法通过界面恢复。
        """
        from app.core.exceptions import NotFound
        from app.core.deps import register_after_commit

        submission = self.submissions.get(session, submission_id)
        if submission is None:
            raise NotFound("提交不存在")

        event_id = submission.event_id
        records = self.files.list_for_submission(session, submission_id)

        # 字节在事务外删除，且只在提交成功后执行——先删字节再提交的话，
        # 一旦回滚就会留下指向不存在文件的记录
        for record in records:
            register_after_commit(
                session,
                lambda event=record.event_id, rel=record.stored_rel: self.storage.delete(
                    event, rel
                ),
            )

        self.events.release_submission_slot(session, event_id)
        session.delete(submission)
        return submission

    def delete_many(self, session: Session, *, submission_ids: Sequence[int]) -> int:
        """批量删除。

        这是罕见路径，因此删完直接按实际行数**重算**该活动的计数，而不是逐条
        递减——重算顺便成为计数器唯一需要的自愈入口。
        """
        from app.core.deps import register_after_commit

        if not submission_ids:
            return 0

        touched_events: set[str] = set()
        deleted = 0

        for submission_id in submission_ids:
            submission = self.submissions.get(session, submission_id)
            if submission is None:
                continue
            touched_events.add(submission.event_id)
            for record in self.files.list_for_submission(session, submission_id):
                register_after_commit(
                    session,
                    lambda event=record.event_id, rel=record.stored_rel: self.storage.delete(
                        event, rel
                    ),
                )
            session.delete(submission)
            deleted += 1

        session.flush()
        for event_id in touched_events:
            self.events.recompute_submission_count(session, event_id)
        return deleted

    def attachment_mime(self, record: SubmissionFile) -> str:
        """由字节嗅探得出，不采信客户端声明的类型。

        仅用于管理端展示：下载响应一律强制为通用二进制类型，因此这个值不参与
        任何安全判定。
        """
        try:
            with self.storage.open(record.event_id, record.stored_rel) as handle:
                return sniff_mime(handle.read(512))
        except Exception:
            return "application/octet-stream"


def _extension(stored_rel: str) -> str | None:
    from pathlib import PurePosixPath

    suffix = PurePosixPath(stored_rel).suffix.lstrip(".")
    return suffix or None


__all__ = ["SubmissionResult", "SubmissionService", "Upload"]
