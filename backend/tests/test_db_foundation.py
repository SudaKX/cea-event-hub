"""任务 2.1 / 2.2：约束命名约定与外键约束实际生效。"""

from __future__ import annotations

from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint, text
from sqlalchemy.schema import ForeignKeyConstraint

from app.core.clock import utcnow
from app.core.enums import EventStatus, StorageState, SubmissionStatus, UserRole
from app.db.base import Base
from app.db.models import Event, Submission, SubmissionFile, User
from app.db.session import Database

CONVENTION_PREFIX = {
    PrimaryKeyConstraint: "pk_",
    ForeignKeyConstraint: "fk_",
    UniqueConstraint: "uq_",
}


class TestNamingConvention:
    """约束名稳定是 MySQL 迁移时能 drop/改约束的前提（可移植性规则第 1 条）。"""

    def test_every_constraint_has_a_conventional_name(self) -> None:
        checked = 0
        for table in Base.metadata.sorted_tables:
            for constraint in table.constraints:
                for constraint_type, prefix in CONVENTION_PREFIX.items():
                    if isinstance(constraint, constraint_type):
                        assert constraint.name, f"{table.name} 存在匿名约束"
                        assert constraint.name.startswith(prefix), (
                            f"{table.name}: {constraint.name} 不符合 {prefix}* 约定"
                        )
                        checked += 1
                        break
        # 六个表都应有主键，外键与唯一约束若干；防止断言在空集合上"通过"
        assert checked >= 15, f"只检查到 {checked} 个约束，元数据可能没加载全"

    def test_all_six_tables_are_registered(self) -> None:
        assert set(Base.metadata.tables) == {
            "users",
            "sessions",
            "user_tokens",
            "events",
            "submissions",
            "submission_files",
        }

    def test_index_names_are_conventional(self) -> None:
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                assert index.name and index.name.startswith("ix_"), index.name

    def test_spot_check_generated_names(self) -> None:
        assert User.__table__.primary_key.name == "pk_users"
        assert Event.__table__.primary_key.name == "pk_events"

        unique_names = {
            c.name
            for t in Base.metadata.sorted_tables
            for c in t.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert unique_names == {
            "uq_users_username",
            "uq_users_email",
            "uq_user_tokens_token_hash",
            "uq_submissions_event_id_idem_key",
        }
        # sessions.token_hash 是主键而非唯一约束，因此不产生 uq_
        assert "pk_sessions" in {t.primary_key.name for t in Base.metadata.sorted_tables}

        fk_names = {
            c.name
            for t in Base.metadata.sorted_tables
            for c in t.constraints
            if isinstance(c, ForeignKeyConstraint)
        }
        assert "fk_sessions_user_id_users" in fk_names
        assert "fk_submission_files_submission_id_submissions" in fk_names


class TestSqlitePragmas:
    """外键约束在 SQLite 上默认关闭；不打开它级联删除会静默失效。"""

    def test_foreign_keys_pragma_is_on(self, test_db: Database) -> None:
        with test_db.session() as session:
            assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def _seed(self, database: Database) -> tuple[int, str]:
        """建出 活动 -> 提交 -> 附件 三级，返回 (submission_id, stored_rel)。"""
        with database.session() as session:
            session.add(
                Event(id="cascade-test", title="级联测试", status=EventStatus.LIVE.value)
            )
            submission = Submission(
                event_id="cascade-test",
                submitter="a:anon",
                payload={"a": 1},
                status=SubmissionStatus.RECEIVED.value,
            )
            session.add(submission)
            session.flush()
            session.add(
                SubmissionFile(
                    submission_id=submission.id,
                    event_id="cascade-test",
                    stored_rel="_default/2026/01/file.txt",
                    original_name="file.txt",
                    size_bytes=3,
                    sha256="deadbeef",
                    storage_state=StorageState.PENDING.value,
                )
            )
            return submission.id, "_default/2026/01/file.txt"

    def test_cascade_delete_removes_child_rows(self, test_db: Database) -> None:
        submission_id, _ = self._seed(test_db)

        with test_db.session() as session:
            session.delete(session.get(Submission, submission_id))

        with test_db.session() as session:
            remaining = session.execute(
                text("SELECT COUNT(*) FROM submission_files")
            ).scalar()
        assert remaining == 0, (
            "删除提交后附件记录仍在——说明 PRAGMA foreign_keys 没有生效，"
            "MySQL 上同样的 schema 却会正确级联，属于最难查的一类差异"
        )

    def test_deleting_event_cascades_to_submissions(self, test_db: Database) -> None:
        self._seed(test_db)

        with test_db.session() as session:
            session.delete(session.get(Event, "cascade-test"))

        with test_db.session() as session:
            submissions = session.execute(
                text("SELECT COUNT(*) FROM submissions")
            ).scalar()
            files = session.execute(text("SELECT COUNT(*) FROM submission_files")).scalar()
        assert (submissions, files) == (0, 0)

    def test_fk_violation_is_rejected(self, test_db: Database) -> None:
        import pytest
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with test_db.session() as session:
                session.add(
                    Submission(
                        event_id="does-not-exist",
                        submitter="a:anon",
                        payload={},
                        status=SubmissionStatus.RECEIVED.value,
                        created_at=utcnow(),
                    )
                )


class TestDatabaseUrlHandling:
    def test_memory_url_uses_static_pool(self) -> None:
        # 内存库必须复用同一条连接，否则每条连接看到的是各自独立的库
        from sqlalchemy.pool import StaticPool

        from app.db.session import create_db_engine

        engine = create_db_engine("sqlite+pysqlite:///:memory:")
        assert isinstance(engine.pool, StaticPool)
        engine.dispose()

    def test_file_url_does_not_use_static_pool(self) -> None:
        from sqlalchemy.pool import StaticPool

        from app.db.session import create_db_engine

        engine = create_db_engine("sqlite+pysqlite:///./var/x.db")
        assert not isinstance(engine.pool, StaticPool)
        engine.dispose()
