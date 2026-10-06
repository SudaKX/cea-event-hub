"""任务 2.3：六张表的列类型、可空性、唯一约束与索引。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import BigInteger, DateTime, String, inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.enums import EventStatus, UserRole
from app.db.models import (
    Event,
    InvitationCode,
    InvitationRedemption,
    Submission,
    User,
)
from app.db.session import Database
from app.db.types import UtcDateTime


def _user(username: str = "alice", **overrides: object) -> User:
    defaults: dict[str, object] = {
        "username": username,
        "display_name": username.title(),
        "password_hash": "argon2-placeholder",
        "role": UserRole.USER.value,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def _event(event_id: str = "spring-2026", **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.DRAFT.value,
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


class TestSchemaShape:
    def test_tables_are_created(self, test_db: Database) -> None:
        names = set(inspect(test_db.engine).get_table_names())
        assert {
            "users",
            "sessions",
            "user_tokens",
            "events",
            "submissions",
            "submission_files",
        } <= names

    def test_indexed_string_columns_have_explicit_length(self) -> None:
        """MySQL 要求索引列必须有长度（可移植性规则第 2 条）。

        只检查参与主键 / 唯一约束 / 索引的列：`Text` 列本身不带长度是合法的，
        前提是它不被索引——把 Text 放进索引才是问题。
        """
        from sqlalchemy import UniqueConstraint

        def constrained_columns(table) -> set[str]:
            names = {c.name for c in table.primary_key.columns}
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint):
                    names |= {c.name for c in constraint.columns}
            for index in table.indexes:
                names |= {c.name for c in index.columns}
            return names

        from app.db.base import Base

        for table in Base.metadata.sorted_tables:
            for name in constrained_columns(table):
                column = table.c[name]
                if not isinstance(column.type, String):
                    continue  # 整数主键等不参与"索引列必须有长度"这条
                assert column.type.length is not None, (
                    f"{table.name}.{name} 参与索引却没声明长度，迁移到 MySQL 会失败"
                )

    def test_size_bytes_is_bigint(self) -> None:
        from app.db.models import SubmissionFile

        column = SubmissionFile.__table__.c.size_bytes
        assert isinstance(column.type, BigInteger)

    def test_timestamp_columns_use_utc_type(self) -> None:
        for table in (User.__table__, Event.__table__, Submission.__table__):
            for column in table.columns:
                if isinstance(column.type, DateTime):
                    assert isinstance(column.type, UtcDateTime), (
                        f"{table.name}.{column.name} 使用了裸 DateTime，"
                        "跨库会退化成 naive 时间"
                    )

    def test_event_defaults(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_event())
        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
        assert event is not None
        assert event.entry_path == "index.html"
        assert event.content_version == 0
        assert event.submission_requires_login is False
        assert event.submission_count == 0
        assert event.max_submissions is None
        assert event.status == EventStatus.DRAFT.value

    def test_user_defaults(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_user())
        with test_db.session() as session:
            user = session.scalar(
                __import__("sqlalchemy").select(User).where(User.username == "alice")
            )
        assert user is not None
        assert user.role == UserRole.USER.value
        assert user.is_active is True
        assert user.email is None
        assert user.email_verified_at is None


class TestUniqueConstraints:
    def test_username_is_unique(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_user("alice"))
        with pytest.raises(IntegrityError):
            with test_db.session() as session:
                session.add(_user("alice"))

    def test_multiple_users_may_have_null_email(self, test_db: Database) -> None:
        # 两库都把 NULL 视为互不相同，因此可空唯一是安全的
        with test_db.session() as session:
            session.add(_user("alice"))
            session.add(_user("bob"))
        with test_db.session() as session:
            assert session.query(User).count() == 2

    def test_email_is_unique_when_present(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_user("alice", email="a@example.com"))
        with pytest.raises(IntegrityError):
            with test_db.session() as session:
                session.add(_user("bob", email="a@example.com"))


class TestIdempotencyKeyConstraint:
    def _submission(self, *, idem_key: str | None) -> Submission:
        return Submission(
            event_id="spring-2026",
            submitter="a:anon",
            payload={},
            idem_key=idem_key,
        )

    def _seed_event(self, session) -> None:
        session.add(_event())

    def test_same_idempotency_key_is_rejected(self, test_db: Database) -> None:
        with test_db.session() as session:
            self._seed_event(session)
            session.add(self._submission(idem_key="key-1"))
        with pytest.raises(IntegrityError):
            with test_db.session() as session:
                session.add(self._submission(idem_key="key-1"))

    def test_null_idempotency_keys_do_not_collide(self, test_db: Database) -> None:
        # 未提供幂等键的提交可以有多条 —— 幂等键是可选的，重复提交不是错误
        with test_db.session() as session:
            self._seed_event(session)
            session.add(self._submission(idem_key=None))
            session.add(self._submission(idem_key=None))
        with test_db.session() as session:
            assert session.query(Submission).count() == 2

    def test_same_key_across_events_is_allowed(self, test_db: Database) -> None:
        # 幂等键的作用域是活动内，不同活动可以复用同一个键
        with test_db.session() as session:
            session.add(_event("spring-2026"))
            session.add(_event("autumn-2026"))
            session.add(self._submission(idem_key="key-1"))
            session.add(
                Submission(
                    event_id="autumn-2026",
                    submitter="a:anon",
                    payload={},
                    idem_key="key-1",
                )
            )
        with test_db.session() as session:
            assert session.query(Submission).count() == 2


class TestUtcDateTimeRoundTrip:
    """两个库的 DATETIME 都不带时区，边界必须由类型收死。"""

    def test_aware_utc_survives_round_trip(self, test_db: Database) -> None:
        moment = datetime(2026, 3, 1, 12, 30, 45, tzinfo=UTC)
        with test_db.session() as session:
            session.add(_event(created_at=moment))
        with test_db.session() as session:
            event = session.get(Event, "spring-2026")

        assert event is not None
        assert event.created_at.tzinfo is not None, "读回来是 naive 时间，跨库比较会抛错"
        assert event.created_at == moment

    def test_non_utc_offset_is_normalized(self, test_db: Database) -> None:
        from datetime import timezone

        tokyo = timezone(timedelta(hours=9))
        moment = datetime(2026, 3, 1, 21, 0, 0, tzinfo=tokyo)  # == 12:00 UTC
        with test_db.session() as session:
            session.add(_event(created_at=moment))
        with test_db.session() as session:
            event = session.get(Event, "spring-2026")

        assert event is not None
        assert event.created_at == datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

    def test_defaults_are_aware(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_user())
        with test_db.session() as session:
            user = session.query(User).one()
        assert user.created_at.tzinfo is not None
        assert user.updated_at.tzinfo is not None

    def test_updated_at_changes_on_update(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_event())
        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
            assert event is not None
            before = event.updated_at
            event.title = "改名后的标题"
        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
        assert event is not None
        assert event.updated_at >= before


def _code(token: str = "INVITE01", **overrides: object) -> InvitationCode:
    defaults: dict[str, object] = {
        "token": token,
        "name": "给张三",
        "owner_id": None,
        "max_uses": 1,
        "used_count": 0,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
    }
    defaults.update(overrides)
    return InvitationCode(**defaults)  # type: ignore[arg-type]


class TestInvitationSchema:
    """邀请码两张表的形状（任务 1.1 / 1.2）。"""

    def test_tables_are_created(self, test_db: Database) -> None:
        names = set(inspect(test_db.engine).get_table_names())
        assert {"invitation_codes", "invitation_redemptions", "platform_switches"} <= names

    def test_defaults(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_code())
        with test_db.session() as session:
            code = session.scalars(select(InvitationCode)).one()
        # 一次一用、还没用过、还没被作废
        assert code.max_uses == 1
        assert code.used_count == 0
        assert code.revoked_at is None
        assert code.created_at.tzinfo is not None

    def test_token_is_unique(self, test_db: Database) -> None:
        with test_db.session() as session:
            session.add(_code("SAME"))
        with pytest.raises(IntegrityError):
            with test_db.session() as session:
                session.add(_code("SAME", name="另一个名字"))

    def test_owner_is_optional(self, test_db: Database) -> None:
        """空 owner 就是**平台码** —— 管理员建的码不归任何个人（design.md 决策 8）。"""
        with test_db.session() as session:
            user = _user()
            session.add(user)
            session.flush()
            session.add(_code("PLATFORM", owner_id=None))
            session.add(_code("PERSONAL", owner_id=user.id))
        with test_db.session() as session:
            by_token = {
                c.token: c.owner_id for c in session.scalars(select(InvitationCode))
            }
        assert by_token == {"PLATFORM": None, "PERSONAL": 1}

    def test_a_code_can_be_used_more_than_once(self, test_db: Database) -> None:
        """`max_uses > 1` 时"被谁用过"是一对多 —— 这正是记录独立成表的理由。"""
        with test_db.session() as session:
            code = _code("MULTI", max_uses=5)
            alice = _user("alice")
            bob = _user("bob")
            session.add_all([code, alice, bob])
            session.flush()
            session.add_all(
                [
                    InvitationRedemption(code_id=code.id, user_id=alice.id),
                    InvitationRedemption(code_id=code.id, user_id=bob.id),
                ]
            )
        with test_db.session() as session:
            records = session.scalars(select(InvitationRedemption)).all()
        assert len(records) == 2

    def test_deleting_the_code_takes_the_records_with_it(
        self, test_db: Database
    ) -> None:
        """级联删除是刻意的：**所以管理员只失效、不删除**（决策 9）。

        用户能删的只有"尚未使用"的码，那时本来就没有记录；一旦有过记录，走的是
        `revoked_at`，物理删除不该发生。
        """
        with test_db.session() as session:
            code = _code()
            user = _user()
            session.add_all([code, user])
            session.flush()
            session.add(InvitationRedemption(code_id=code.id, user_id=user.id))
        with test_db.session() as session:
            session.delete(session.scalars(select(InvitationCode)).one())
        with test_db.session() as session:
            assert session.scalars(select(InvitationRedemption)).all() == []

    def test_deleting_the_user_keeps_the_record(self, test_db: Database) -> None:
        """发码人注销后，"这张码被谁用了"必须还在 —— 记录里只少了他的名字。

        与 `submissions.user_id` 同一个取向：账号删除不该抹掉社团收集到的事实。
        """
        with test_db.session() as session:
            code = _code()
            owner = _user("owner")
            guest = _user("guest")
            session.add_all([code, owner, guest])
            session.flush()
            session.add(InvitationRedemption(code_id=code.id, user_id=guest.id))
            code.owner_id = owner.id
        with test_db.session() as session:
            session.delete(
                session.scalars(select(User).where(User.username == "owner")).one()
            )
        with test_db.session() as session:
            record = session.scalars(select(InvitationRedemption)).one()
            remaining = session.scalars(select(InvitationCode)).one()
        assert record.user_id is not None, "使用记录被牵连删掉了"
        assert remaining.owner_id is None, "归属应变为不可考，而不是连带删除"
