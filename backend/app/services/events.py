"""活动的业务规则。

本模块不 import fastapi。活动标识的不可变性、配额上限的求值规则、以及
"公开接口只暴露 live 活动"这三条都在这里，而不是散落在路由里。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.enums import EventStatus, EventVisibility
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.core.text import (
    entry_path_shape_error,
    event_id_shape_error,
    normalize_event_id,
)
from app.db.models import Event, User
from app.repositories.events import EventRepository
from app.schemas.events import QuotaState

DEFAULT_ENTRY_PATH = "index.html"


class EventService:
    def __init__(
        self, settings: Settings, repo: EventRepository | None = None
    ) -> None:
        self.settings = settings
        self.repo = repo or EventRepository()

    # ------------------------------------------------------------------
    # 配额上限求值（task 6.4）
    # ------------------------------------------------------------------

    def effective_quota_limit(self, event: Event) -> int | None:
        """活动级覆盖优先，否则按是否需要登录取全局默认。

        可匿名提交的活动默认封顶 4096 条；需登录的活动默认不限额——登录本身
        就是一层问责，再叠加硬上限只会挡住正常使用。
        """
        if event.max_submissions is not None:
            return event.max_submissions
        if not event.submission_requires_login:
            return self.settings.MAX_SUBMISSIONS_PER_EVENT_ANON
        return self.settings.MAX_SUBMISSIONS_PER_EVENT_AUTHED

    def quota_state(self, event: Event) -> QuotaState:
        limit = self.effective_quota_limit(event)
        used = event.submission_count
        return QuotaState(
            limit=limit,
            used=used,
            remaining=None if limit is None else max(limit - used, 0),
        )

    # ------------------------------------------------------------------
    # 提交窗口（供提交服务复用）
    # ------------------------------------------------------------------

    def submission_window_error(self, event: Event) -> str | None:
        """返回不能提交的原因；可以提交时返回 None。"""
        if event.status != EventStatus.LIVE.value:
            return "event_not_live"
        now = utcnow()
        if event.submissions_open_at is not None and now < event.submissions_open_at:
            return "not_yet_open"
        if event.submissions_close_at is not None and now > event.submissions_close_at:
            return "closed_at"
        return None

    # ------------------------------------------------------------------
    # 公开读取（task 6.2）
    # ------------------------------------------------------------------

    def list_public(self, session: Session) -> list[Event]:
        """公开目录。**不可见（0）的活动不在其中**，但按标识仍可直接打开。

        含"公开（1）"与"公开并置顶（2）"两档，置顶的排在前面。
        """
        return list(
            session.scalars(
                self.repo.list_public(session, status=EventStatus.LIVE.value)
            ).all()
        )

    def get_public(self, session: Session, event_id: str) -> Event:
        """只返回 live 活动。

        对未发布的活动返回 404 而不是 403：后者会泄露"这个标识存在但还没上线"。

        **不可见的活动在这里照常放行** —— 它只是不出现在公开面。可见性不是访问
        控制：标识本身就是那条链接，管理员把它发给谁，谁就能打开。
        """
        event = self.repo.get(session, event_id)
        if event is None or event.status != EventStatus.LIVE.value:
            raise NotFound("活动不存在")
        return event

    # ------------------------------------------------------------------
    # 管理端写入（task 6.1 / 6.3）
    # ------------------------------------------------------------------

    def create(
        self,
        session: Session,
        *,
        event_id: str,
        title: str,
        summary: str | None = None,
        entry_path: str | None = None,
        submission_requires_login: bool | None = None,
        submissions_open_at=None,
        submissions_close_at=None,
        max_submissions: int | None = None,
        max_per_submitter: int | None = None,
        visibility: str | None = None,
        owner: User | None = None,
    ) -> Event:
        # **先归一化、再校验、并用归一化后的值落盘。** 这三步的顺序不能换：
        # 标识直接成为内容与数据目录名，若校验的是 A 而落盘的是 B，路径安全就退化成
        # "依赖某个语言 lower() 的具体行为"。见 core/text.normalize_event_id。
        #
        # 归一化在 service 而不是路由：这里是唯一的写入口，放在这里才不存在第二条
        # 绕过路径（标识来自请求体，不是路径参数，路由层的依赖也覆盖不到它）。
        normalized_id = normalize_event_id(event_id)

        fields: dict[str, str] = {}
        if (problem := event_id_shape_error(normalized_id)) is not None:
            fields["id"] = problem

        path = entry_path or DEFAULT_ENTRY_PATH
        if (problem := entry_path_shape_error(path)) is not None:
            fields["entry_path"] = problem

        if fields:
            raise ValidationFailed(fields=fields)

        if visibility is not None and visibility not in {v.value for v in EventVisibility}:
            raise ValidationFailed(fields={"visibility": "可见性取值不合法"})

        if self.repo.get(session, normalized_id) is not None:
            raise Conflict("该活动标识已被占用")

        event = Event(
            id=normalized_id,
            title=title.strip(),
            summary=summary,
            status=EventStatus.DRAFT.value,
            # 不给就按"公开"建：与加这个字段之前的行为一致。
            # **不能写 `visibility or PUBLIC`** —— 码值 0（不公开）是合法取值，
            # 用 or 兜底会把它悄悄换成 1，于是"不公开"永远设不上。
            visibility=(
                EventVisibility.PUBLIC.value if visibility is None else visibility
            ),
            entry_path=path,
            content_version=0,
            submission_count=0,
            owner_id=owner.id if owner else None,
            # 未指定时取全局种子值；此后以活动字段为唯一真源
            submission_requires_login=(
                self.settings.SUBMISSION_REQUIRES_LOGIN_SEED
                if submission_requires_login is None
                else submission_requires_login
            ),
            submissions_open_at=submissions_open_at,
            submissions_close_at=submissions_close_at,
            max_submissions=max_submissions,
            max_per_submitter=max_per_submitter,
        )
        self.repo.add(session, event)
        return event

    def update(
        self, session: Session, *, event_id: str, changes: dict[str, object]
    ) -> Event:
        event = self.repo.get(session, event_id)
        if event is None:
            raise NotFound("活动不存在")

        if "id" in changes:
            # 路由层用 extra="forbid" 已经挡掉，这里是纵深防御
            raise ValidationFailed(fields={"id": "活动标识不可修改"})

        if "entry_path" in changes:
            path = str(changes["entry_path"])
            if (problem := entry_path_shape_error(path)) is not None:
                raise ValidationFailed(fields={"entry_path": problem})

        if "status" in changes:
            status_value = str(changes["status"])
            if status_value not in {s.value for s in EventStatus}:
                raise ValidationFailed(fields={"status": "状态取值不合法"})

        if "visibility" in changes:
            # 码值是整数，别 stringify —— `str(1)` 会变成 "1"，与枚举值比不相等
            if changes["visibility"] not in {v.value for v in EventVisibility}:
                raise ValidationFailed(fields={"visibility": "可见性取值不合法"})

        if (
            changes.get("submissions_open_at")
            and changes.get("submissions_close_at")
            and changes["submissions_open_at"] > changes["submissions_close_at"]
        ):
            raise ValidationFailed(
                fields={"submissions_close_at": "截止时间不能早于开放时间"}
            )

        for key, value in changes.items():
            setattr(event, key, value)
        return event

    def set_status(self, session: Session, *, event_id: str, status: str) -> Event:
        return self.update(session, event_id=event_id, changes={"status": status})

    def delete(self, session: Session, *, event_id: str) -> Event:
        """物理删除。

        归档（status=archived）是默认手段；物理删除仅由管理员显式发起，并会
        级联移除该活动的提交与附件记录（内容与数据目录由内容服务另行清理）。
        """
        event = self.repo.get(session, event_id)
        if event is None:
            raise NotFound("活动不存在")
        self.repo.delete(session, event)
        return event

    def list_admin(self, session: Session, *, status: str | None = None) -> list[Event]:
        return list(session.scalars(self.repo.list_all(session, status=status)).all())


__all__ = ["DEFAULT_ENTRY_PATH", "EventService"]
