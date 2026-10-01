"""提交与附件的持久化。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Submission, SubmissionFile


class SubmissionRepository:
    def get(self, session: Session, submission_id: int) -> Submission | None:
        return session.get(Submission, submission_id)

    def add(self, session: Session, submission: Submission) -> Submission:
        session.add(submission)
        session.flush()
        return submission

    def delete(self, session: Session, submission: Submission) -> None:
        session.delete(submission)

    def count_by_event(self, session: Session, event_id: str) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.event_id == event_id)
            )
            or 0
        )

    def find_by_idem_key(
        self, session: Session, *, event_id: str, idem_key: str
    ) -> Submission | None:
        """幂等键的作用域是**活动内**：不同活动可以复用同一个键。"""
        return session.scalar(
            select(Submission).where(
                Submission.event_id == event_id,
                Submission.idem_key == idem_key,
            )
        )

    def list_for_event(
        self,
        session: Session,
        *,
        event_id: str,
        kind: str | None = None,
        status: str | None = None,
        submitter: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> Select:
        statement = (
            select(Submission)
            .where(Submission.event_id == event_id)
            .order_by(Submission.created_at.desc(), Submission.id.desc())
        )
        if kind is not None:
            statement = statement.where(Submission.kind == kind)
        if status is not None:
            statement = statement.where(Submission.status == status)
        if submitter is not None:
            statement = statement.where(Submission.submitter == submitter)
        if created_from is not None:
            statement = statement.where(Submission.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(Submission.created_at <= created_to)
        return statement

    def list_for_submitter(
        self, session: Session, *, submitter: str, event_id: str | None = None
    ) -> Select:
        statement = (
            select(Submission)
            .where(Submission.submitter == submitter)
            .order_by(Submission.created_at.desc(), Submission.id.desc())
        )
        if event_id is not None:
            statement = statement.where(Submission.event_id == event_id)
        return statement

    def count_all(self, session: Session) -> int:
        return session.scalar(select(func.count()).select_from(Submission)) or 0


class SubmissionFileRepository:
    def add(self, session: Session, record: SubmissionFile) -> SubmissionFile:
        session.add(record)
        return record

    def get(self, session: Session, file_id: int) -> SubmissionFile | None:
        return session.get(SubmissionFile, file_id)

    def delete(self, session: Session, record: SubmissionFile) -> None:
        session.delete(record)

    def list_pending_before(
        self, session: Session, *, cutoff: datetime, state: str
    ) -> list[SubmissionFile]:
        """超时仍未完成的落盘记录。

        进程在"写盘"与"落库"之间中断时会留下这些行；不回收它们会永久占用
        配额并留下无人认领的字节。
        """
        return list(
            session.scalars(
                select(SubmissionFile).where(
                    SubmissionFile.storage_state == state,
                    SubmissionFile.created_at < cutoff,
                )
            ).all()
        )

    def list_for_submission(
        self, session: Session, submission_id: int
    ) -> list[SubmissionFile]:
        return list(
            session.scalars(
                select(SubmissionFile)
                .where(SubmissionFile.submission_id == submission_id)
                .order_by(SubmissionFile.id)
            ).all()
        )

    def delete_for_submission(self, session: Session, submission_id: int) -> int:
        result = session.execute(
            delete(SubmissionFile).where(SubmissionFile.submission_id == submission_id)
        )
        return int(result.rowcount or 0)

    def total_bytes_for_event(self, session: Session, event_id: str) -> int:
        return (
            session.scalar(
                select(func.coalesce(func.sum(SubmissionFile.size_bytes), 0)).where(
                    SubmissionFile.event_id == event_id
                )
            )
            or 0
        )

    def known_paths(self, session: Session) -> set[tuple[str, str]]:
        """全部 (event_id, stored_rel)。供 janitor 判断磁盘上哪些文件无人认领。"""
        rows = session.execute(
            select(SubmissionFile.event_id, SubmissionFile.stored_rel)
        ).all()
        return {(row[0], row[1]) for row in rows}
