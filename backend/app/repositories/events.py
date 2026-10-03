"""活动的持久化。"""

from __future__ import annotations

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.core.enums import EventVisibility
from app.db.models import Event


class EventRepository:
    def get(self, session: Session, event_id: str) -> Event | None:
        return session.get(Event, event_id)

    def add(self, session: Session, event: Event) -> Event:
        session.add(event)
        session.flush()
        return event

    def delete(self, session: Session, event: Event) -> None:
        session.delete(event)

    def list_public(self, session: Session, *, status: str) -> Select:
        """公开目录：已发布且**在公开面露过面**（可见性 > 0）的活动。

        置顶的排前面，其次按标识。排序放在这里而不是让调用方再排一遍，
        免得两处顺序不一致。
        """
        return (
            select(Event)
            .where(
                Event.status == status,
                Event.visibility > EventVisibility.INVISIBLE.value,
            )
            .order_by(Event.visibility.desc(), Event.id)
        )

    def list_all(self, session: Session, *, status: str | None = None) -> Select:
        statement = select(Event).order_by(Event.created_at.desc())
        if status is not None:
            statement = statement.where(Event.status == status)
        return statement

    def count(self, session: Session) -> int:
        return session.scalar(select(func.count()).select_from(Event)) or 0

    # ------------------------------------------------------------------
    # 配额计数：单语句 CAS（design.md 决策 9）
    #
    # 为什么不是 COUNT(*) 再插入：那样要精确就必须在事务开始时取写锁，需要
    # 在仓储层写方言分支（SQLite 的 BEGIN IMMEDIATE / MySQL 的 FOR UPDATE）。
    # CAS 是一条语句内的原子操作，任何隔离级别、任何数据库都成立，**迁移面上
    # 少一个接缝**。用 rowcount 而非 RETURNING，因为 MySQL 不支持 RETURNING。
    #
    # 注意 `submission_count = submission_count + 1` 必然改变列值，所以
    # MySQL 的 rowcount"匹配行数 vs 改变行数"差异在这里不会咬到我们。
    # ------------------------------------------------------------------

    def try_acquire_submission_slot(
        self, session: Session, event_id: str, *, limit: int
    ) -> bool:
        """尝试占用一个提交名额。返回 False 表示已达上限。"""
        result = session.execute(
            update(Event)
            .where(Event.id == event_id, Event.submission_count < limit)
            .values(submission_count=Event.submission_count + 1)
        )
        return bool(result.rowcount)

    def increment_submission_count(self, session: Session, event_id: str) -> None:
        """无上限时仍计数，供管理端展示已收条数。"""
        session.execute(
            update(Event)
            .where(Event.id == event_id)
            .values(submission_count=Event.submission_count + 1)
        )

    def release_submission_slot(self, session: Session, event_id: str) -> None:
        """释放一个名额。

        单条删除必须调用它，且必须与删除在同一事务内：这是管理员在满额活动上
        唯一的自救手段，不递减会让活动被永久锁死且无法通过界面恢复。
        下限钳到 0，避免数据异常时出现负数。
        """
        session.execute(
            update(Event)
            .where(Event.id == event_id, Event.submission_count > 0)
            .values(submission_count=Event.submission_count - 1)
        )

    def recompute_submission_count(self, session: Session, event_id: str) -> int:
        """按实际行数重算计数。

        批量删除等罕见路径调用；也是计数器唯一需要的自愈入口。
        """
        from app.db.models import Submission

        actual = (
            session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.event_id == event_id)
            )
            or 0
        )
        session.execute(
            update(Event).where(Event.id == event_id).values(submission_count=actual)
        )
        return actual

    def bump_content_version(self, session: Session, event_id: str) -> int:
        session.execute(
            update(Event)
            .where(Event.id == event_id)
            .values(content_version=Event.content_version + 1)
        )
        session.expire_all()
        event = session.get(Event, event_id)
        return event.content_version if event else 0
