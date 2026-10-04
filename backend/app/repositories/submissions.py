"""提交与附件的持久化。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, String, cast, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Submission, SubmissionFile, SubmitterQuota

#: LIKE 的转义符。反斜杠是 SQL 的惯例，SQLite 与 MySQL 都认。
_LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """把用户输入里的 LIKE 元字符转义掉。

    不转义的话搜 `%` 会匹配全部、搜 `_` 会匹配任意单字符 —— 用户以为自己搜了一个
    具体符号，实际得到的是通配。反斜杠要先转，否则会把后面补的转义符再转一次。
    """
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


class SubmitterQuotaRepository:
    """单个提交者的份数计数器。

    与 `EventRepository` 的配额段同一套理由：**用单语句 CAS 而不是"数一下再写"**。
    数一下再写在 SQLite 上会因快照冲突报错（安全但难看），在 MySQL 的可重复读下
    则两个并发请求各自数到 0、双双插入，**静默超限**。CAS 在一条语句内完成判断与
    写入，任何隔离级别、任何数据库都成立，迁移面上少一个接缝。

    用 rowcount 而不是 RETURNING：MySQL 不支持 RETURNING。这里的
    `used = used + 1` 必然改变列值，所以 MySQL 的 rowcount"匹配行数 vs 改变行数"
    差异不会咬到我们。
    """

    def try_acquire(
        self, session: Session, event_id: str, submitter: str, *, limit: int
    ) -> bool:
        """占用这个提交者的一个份数。返回 False 表示已达上限。

        两步：先试更新既有行；更新不到再试插入。**插入撞唯一键是有意义的信号**
        ——它说明行是刚刚被并发的另一个请求建出来的，此时应当退回 CAS 重试，而
        不是直接放行。
        """
        if self._increment(session, event_id, submitter, limit=limit):
            return True

        # 更新不到有两种可能：行不存在，或行已满。用插入来区分。
        try:
            with session.begin_nested():
                session.add(
                    SubmitterQuota(event_id=event_id, submitter=submitter, used=1)
                )
        except IntegrityError:
            # 行已存在（并发的另一个请求刚建出来），退回 CAS 重试一次
            return self._increment(session, event_id, submitter, limit=limit)
        return True

    def _increment(
        self, session: Session, event_id: str, submitter: str, *, limit: int
    ) -> bool:
        result = session.execute(
            update(SubmitterQuota)
            .where(
                SubmitterQuota.event_id == event_id,
                SubmitterQuota.submitter == submitter,
                SubmitterQuota.used < limit,
            )
            .values(used=SubmitterQuota.used + 1)
        )
        return bool(result.rowcount)

    def count_for(
        self, session: Session, event_id: str, submitter: str
    ) -> int:
        return (
            session.scalar(
                select(SubmitterQuota.used).where(
                    SubmitterQuota.event_id == event_id,
                    SubmitterQuota.submitter == submitter,
                )
            )
            or 0
        )

    def release(self, session: Session, event_id: str, submitter: str) -> None:
        """退还一份。

        与删除同一事务：不退还的话，管理员删掉一条也救不回那个人 —— 与活动级
        配额"删一条腾一个名额"是同一条后路。下限钳到 0，避免数据异常时出现负数。
        """
        session.execute(
            update(SubmitterQuota)
            .where(
                SubmitterQuota.event_id == event_id,
                SubmitterQuota.submitter == submitter,
                SubmitterQuota.used > 0,
            )
            .values(used=SubmitterQuota.used - 1)
        )

    def delete_for_submitter(self, session: Session, submitter: str) -> int:
        """删掉某个提交者在**所有活动**下的计数行。

        删除账号时调用。那些行没有外键承托（`submitter` 是派生字符串，不是 user_id），
        因此不会随账号消失，需要显式清理。

        留着其实也不会造成误判 —— id 不再复用之后，`u:{id}` 是个永久标识，不会再有
        人拿到它。清理的理由是可观测性：`var/inspect_dev_db.py` 的一致性检查会把
        "计数 N、实际 0"一直报成不符，让一个真正的一致性告警淹没在噪音里。
        """
        result = session.execute(
            delete(SubmitterQuota).where(SubmitterQuota.submitter == submitter)
        )
        return int(result.rowcount or 0)

    def recompute(
        self, session: Session, event_id: str, submitter: str
    ) -> int:
        """按实际行数重算某个提交者的份数。

        批量删除等罕见路径调用；也是这个计数器唯一需要的自愈入口。
        """
        actual = (
            session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(
                    Submission.event_id == event_id,
                    Submission.submitter == submitter,
                )
            )
            or 0
        )
        existing = session.get(SubmitterQuota, (event_id, submitter))
        if existing is None:
            if actual:
                session.add(
                    SubmitterQuota(event_id=event_id, submitter=submitter, used=actual)
                )
            return actual
        existing.used = actual
        return actual


class SubmissionRepository:
    def get(self, session: Session, submission_id: int) -> Submission | None:
        return session.get(Submission, submission_id)

    def list_by_ids(
        self, session: Session, submission_ids: Sequence[int]
    ) -> list[Submission]:
        """一次取回多条。

        批量端点存在的意义就是省掉逐条往返 —— 循环里调 `get` 的话，
        "批量"只是把 N 次请求换成了 N 次查询，没省下什么。
        """
        if not submission_ids:
            return []
        return list(
            session.scalars(
                select(Submission).where(Submission.id.in_(list(submission_ids)))
            ).all()
        )

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
        status: int | None = None,
        submitter: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        payload_contains: str | None = None,
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
        if payload_contains:
            # 对内容做子串匹配，而不是解析 JSON 里的某个键 —— payload 没有字段级
            # 契约，活动自己决定放什么，平台无从知道该搜哪个键。
            #
            # `cast(..., String)` 是为了跨库：SQLite 与 MySQL 的 JSON 列都不是可直接
            # LIKE 的文本类型，转成字符串后两边行为一致（方言差异由 SQLAlchemy 吸收，
            # 这里不必写方言 SQL）。
            statement = statement.where(
                cast(Submission.payload, String).like(
                    f"%{escape_like(payload_contains)}%", escape=_LIKE_ESCAPE
                )
            )
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
