"""任务 3.1 / 3.3：仓储的持久化行为与可替换端口的形状。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.enums import EventStatus, EventVisibility, UserRole
from app.core.ports import (
    EmailSender,
    FileStorage,
    NullEmailSender,
    RateLimitDecision,
    RateLimiter,
    StoredFile,
)
from app.core.security import hash_password, hash_token
from app.db.models import Event, User, UserSession, UserToken
from app.repositories.events import EventRepository
from app.repositories.users import SessionRepository, UserRepository, UserTokenRepository


@pytest.fixture
def repos() -> tuple[UserRepository, SessionRepository, EventRepository, UserTokenRepository]:
    return UserRepository(), SessionRepository(), EventRepository(), UserTokenRepository()


def _make_user(session, username: str = "alice", **overrides) -> User:
    defaults: dict[str, object] = {
        "username": username,
        "display_name": username,
        "password_hash": hash_password("pw"),
        "role": UserRole.USER.value,
    }
    defaults.update(overrides)
    user = User(**defaults)  # type: ignore[arg-type]
    session.add(user)
    session.flush()
    return user


def _make_event(session, event_id: str = "spring-2026", **overrides) -> Event:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    event = Event(**defaults)  # type: ignore[arg-type]
    session.add(event)
    session.flush()
    return event


class TestUserRepository:
    def test_add_and_get(self, test_db, repos) -> None:
        user_repo, *_ = repos
        with test_db.session() as session:
            created = user_repo.add(session, _make_user(session))
            user_id = created.id
        with test_db.session() as session:
            fetched = user_repo.get(session, user_id)
        assert fetched is not None and fetched.username == "alice"

    def test_get_by_username_is_exact(self, test_db, repos) -> None:
        # 仓储不做归一化——那是写入方的责任；因此这里必须精确匹配
        user_repo, *_ = repos
        with test_db.session() as session:
            _make_user(session, "alice")
        with test_db.session() as session:
            assert user_repo.get_by_username(session, "alice") is not None
            assert user_repo.get_by_username(session, "Alice") is None

    def test_count_and_count_admins(self, test_db, repos) -> None:
        user_repo, *_ = repos
        with test_db.session() as session:
            _make_user(session, "alice")
            _make_user(session, "bob", role=UserRole.ADMIN.value)
            _make_user(session, "carol", role=UserRole.ADMIN.value, is_active=False)

        with test_db.session() as session:
            assert user_repo.count(session) == 3
            # 只数**启用**的管理员：停用的管理员不该把"最后一个管理员"保护撑住
            assert user_repo.count_admins(session) == 1
            assert user_repo.count_admins(session, exclude_id=2) == 0

    def test_list_users_filters(self, test_db, repos) -> None:
        user_repo, *_ = repos
        with test_db.session() as session:
            _make_user(session, "alice")
            _make_user(session, "bob", role=UserRole.ADMIN.value, is_active=False)

        with test_db.session() as session:
            admins = session.scalars(
                user_repo.list_users(session, role=UserRole.ADMIN.value)
            ).all()
            inactive = session.scalars(
                user_repo.list_users(session, is_active=False)
            ).all()
            partial = session.scalars(
                user_repo.list_users(session, username_like="lic")
            ).all()

        assert [u.username for u in admins] == ["bob"]
        assert [u.username for u in inactive] == ["bob"]
        assert [u.username for u in partial] == ["alice"]


class TestSessionRepository:
    def test_add_and_lookup_by_hash(self, test_db, repos) -> None:
        user_repo, session_repo, *_ = repos
        token = "raw-token-value"
        with test_db.session() as session:
            user = _make_user(session)
            session_repo.add(
                session,
                UserSession(
                    token_hash=hash_token(token),
                    user_id=user.id,
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            )

        with test_db.session() as session:
            record = session_repo.get(session, hash_token(token))
        assert record is not None
        # 库里存的是摘要，不是令牌本身
        assert record.token_hash != token

    def test_revoke_all_for_user_keeps_excluded(self, test_db, repos) -> None:
        user_repo, session_repo, *_ = repos
        with test_db.session() as session:
            user = _make_user(session)
            for name in ("t1", "t2", "t3"):
                session_repo.add(
                    session,
                    UserSession(
                        token_hash=hash_token(name),
                        user_id=user.id,
                        expires_at=utcnow() + timedelta(hours=1),
                    ),
                )

        with test_db.session() as session:
            removed = session_repo.revoke_all_for_user(
                session, user.id, except_token_hash=hash_token("t1")
            )
        assert removed == 2

        with test_db.session() as session:
            assert session_repo.get(session, hash_token("t1")) is not None
            assert session_repo.get(session, hash_token("t2")) is None

    def test_delete_expired_only_removes_past(self, test_db, repos) -> None:
        user_repo, session_repo, *_ = repos
        with test_db.session() as session:
            user = _make_user(session)
            session_repo.add(
                session,
                UserSession(
                    token_hash=hash_token("old"),
                    user_id=user.id,
                    expires_at=utcnow() - timedelta(seconds=1),
                ),
            )
            session_repo.add(
                session,
                UserSession(
                    token_hash=hash_token("fresh"),
                    user_id=user.id,
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            )

        with test_db.session() as session:
            assert session_repo.delete_expired(session, now=utcnow()) == 1
        with test_db.session() as session:
            assert session_repo.get(session, hash_token("fresh")) is not None


class TestUserTokenRepository:
    def test_add_and_lookup(self, test_db, repos) -> None:
        user_repo, _, _, token_repo = repos
        with test_db.session() as session:
            user = _make_user(session)
            token_repo.add(
                session,
                UserToken(
                    user_id=user.id,
                    purpose="password_reset",
                    token_hash=hash_token("reset-token"),
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            )
        with test_db.session() as session:
            found = token_repo.get_by_hash(session, hash_token("reset-token"))
        assert found is not None and found.purpose == "password_reset"

    def test_invalidate_outstanding_is_scoped_by_purpose(self, test_db, repos) -> None:
        user_repo, _, _, token_repo = repos
        with test_db.session() as session:
            user = _make_user(session)
            for purpose in ("password_reset", "email_verify"):
                token_repo.add(
                    session,
                    UserToken(
                        user_id=user.id,
                        purpose=purpose,
                        token_hash=hash_token(purpose),
                        expires_at=utcnow() + timedelta(hours=1),
                    ),
                )

        with test_db.session() as session:
            removed = token_repo.invalidate_outstanding(
                session, user.id, "password_reset"
            )
        assert removed == 1

        with test_db.session() as session:
            assert token_repo.get_by_hash(session, hash_token("password_reset")) is None
            # 另一种用途不受影响
            assert token_repo.get_by_hash(session, hash_token("email_verify")) is not None


class TestPendingRegistrationRepository:
    """核销的 CAS 条件。

    这些条件**在服务层看起来是多余的** —— 服务层已经按 token_hash 取出那一行、
    也已经判过有效期。它们真正防的是"检查"与"删除"之间那一瞬：凭据恰好在两者
    之间过期，或者被并发的另一个请求核销掉。

    单线程的接口测试够不着那个窗口，所以在这里直接断言 —— 否则把条件删掉不会有
    任何测试变红（这一点是变异测试发现的）。
    """

    def _pending(self, session, **overrides):
        from app.db.models import PendingRegistration

        defaults = {
            "username": "alice",
            "email": "alice@example.com",
            "password_hash": "x",
            "display_name": "alice",
            "token_hash": "digest",
            "expires_at": utcnow() + timedelta(minutes=10),
        }
        defaults.update(overrides)
        row = PendingRegistration(**defaults)  # type: ignore[arg-type]
        session.add(row)
        session.flush()
        return row

    def test_claim_succeeds_while_valid(self, test_db) -> None:
        from app.repositories.users import PendingRegistrationRepository

        repo = PendingRegistrationRepository()
        with test_db.session() as session:
            row = self._pending(session)
            assert repo.claim(
                session, pending_id=row.id, token_hash="digest", now=utcnow()
            )
            assert repo.get_by_token_hash(session, "digest") is None

    def test_claim_refuses_an_expired_row(self, test_db) -> None:
        # 判过有效期之后、删除之前恰好过期：这一条必须自己把住
        from app.repositories.users import PendingRegistrationRepository

        repo = PendingRegistrationRepository()
        with test_db.session() as session:
            row = self._pending(session, expires_at=utcnow() - timedelta(seconds=1))
            assert not repo.claim(
                session, pending_id=row.id, token_hash="digest", now=utcnow()
            )
            assert repo.get_by_token_hash(session, "digest") is not None

    def test_claim_refuses_a_mismatched_digest(self, test_db) -> None:
        from app.repositories.users import PendingRegistrationRepository

        repo = PendingRegistrationRepository()
        with test_db.session() as session:
            row = self._pending(session)
            assert not repo.claim(
                session, pending_id=row.id, token_hash="another", now=utcnow()
            )

    def test_claim_is_single_use(self, test_db) -> None:
        """并发的第二次核销必须拿到 False，而不是删掉另一个人的占位。"""
        from app.repositories.users import PendingRegistrationRepository

        repo = PendingRegistrationRepository()
        with test_db.session() as session:
            row = self._pending(session, token_hash="once")
            now = utcnow()
            assert repo.claim(session, pending_id=row.id, token_hash="once", now=now)
            assert not repo.claim(session, pending_id=row.id, token_hash="once", now=now)

    def test_conflicting_cleanup_only_touches_expired_rows(self, test_db) -> None:
        """请求时清理**只**该删过期的冲突行。

        条件少一层就会变成 `A OR (B AND C)`，把"用户名冲突但仍在有效期内"的占位
        也一起删掉 —— 那等于替别人撤销了预留。

        两行**不能同名**（`username` 唯一），所以冲突只能来自另一列：新请求的
        用户名撞上一条有效的，邮箱撞上一条过期的。
        """
        from app.repositories.users import PendingRegistrationRepository

        repo = PendingRegistrationRepository()
        with test_db.session() as session:
            live = self._pending(
                session,
                username="alice",
                email="live@example.com",
                token_hash="live-digest",
            )
            expired = self._pending(
                session,
                username="bob",
                email="reused@example.com",
                token_hash="expired-digest",
                expires_at=utcnow() - timedelta(seconds=1),
            )

            removed = repo.delete_expired_conflicting(
                session,
                username="alice",
                email="reused@example.com",
                now=utcnow(),
            )

            assert removed == 1
            # 有效的那条必须留下 —— 它占的用户名还归它
            assert repo.get_by_token_hash(session, live.token_hash) is not None
            assert repo.get_by_token_hash(session, expired.token_hash) is None


class TestEventRepository:
    def test_list_public_only_returns_requested_status(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "live-1", status=EventStatus.LIVE.value)
            _make_event(session, "draft-1", status=EventStatus.DRAFT.value)
            _make_event(session, "archived-1", status=EventStatus.ARCHIVED.value)

        with test_db.session() as session:
            public = session.scalars(
                event_repo.list_public(session, status=EventStatus.LIVE.value)
            ).all()
        assert [e.id for e in public] == ["live-1"]

    def test_list_public_excludes_invisible_events(self, test_db, repos) -> None:
        """不可见的活动不进目录 —— 但它是"未公开"，不是"不存在"，仍可按标识取到。"""
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "listed", status=EventStatus.LIVE.value)
            _make_event(
                session,
                "unlisted",
                status=EventStatus.LIVE.value,
                visibility=EventVisibility.INVISIBLE.value,
            )

        with test_db.session() as session:
            public = session.scalars(
                event_repo.list_public(session, status=EventStatus.LIVE.value)
            ).all()
            # 直接取仍然拿得到
            unlisted = event_repo.get(session, "unlisted")
        assert [e.id for e in public] == ["listed"]
        assert unlisted is not None

    def test_count(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "a")
            _make_event(session, "b")
        with test_db.session() as session:
            assert event_repo.count(session) == 2

    def test_bump_content_version(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "e1")
        with test_db.session() as session:
            assert event_repo.bump_content_version(session, "e1") == 1
        with test_db.session() as session:
            assert event_repo.bump_content_version(session, "e1") == 2


class TestQuotaCounter:
    """单语句 CAS 的语义（design.md 决策 9）。"""

    def test_acquire_up_to_limit_then_refuse(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "capped")

        with test_db.session() as session:
            assert event_repo.try_acquire_submission_slot(session, "capped", limit=3)
            assert event_repo.try_acquire_submission_slot(session, "capped", limit=3)
            assert event_repo.try_acquire_submission_slot(session, "capped", limit=3)
            # 第四个必须被拒
            assert not event_repo.try_acquire_submission_slot(session, "capped", limit=3)

        with test_db.session() as session:
            assert session.get(Event, "capped").submission_count == 3

    def test_refused_acquire_does_not_increment(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "capped", submission_count=5)
        with test_db.session() as session:
            assert not event_repo.try_acquire_submission_slot(session, "capped", limit=5)
        with test_db.session() as session:
            assert session.get(Event, "capped").submission_count == 5

    def test_release_frees_a_slot(self, test_db, repos) -> None:
        """管理员"删一条腾一个名额"这条后路依赖它。"""
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "capped", submission_count=2)

        with test_db.session() as session:
            assert not event_repo.try_acquire_submission_slot(session, "capped", limit=2)
            event_repo.release_submission_slot(session, "capped")
            # 释放后立刻可以再占一个
            assert event_repo.try_acquire_submission_slot(session, "capped", limit=2)

        with test_db.session() as session:
            assert session.get(Event, "capped").submission_count == 2

    def test_release_never_goes_negative(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "capped", submission_count=0)
        with test_db.session() as session:
            event_repo.release_submission_slot(session, "capped")
        with test_db.session() as session:
            assert session.get(Event, "capped").submission_count == 0

    def test_increment_without_limit(self, test_db, repos) -> None:
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "open")
        with test_db.session() as session:
            event_repo.increment_submission_count(session, "open")
        with test_db.session() as session:
            assert session.get(Event, "open").submission_count == 1

    def test_recompute_matches_actual_rows(self, test_db, repos) -> None:
        from app.db.models import Submission

        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "drifted", submission_count=999)
            for index in range(3):
                session.add(
                    Submission(
                        event_id="drifted",
                        submitter="a:anon",
                        payload={},
                    )
                )

        with test_db.session() as session:
            assert event_repo.recompute_submission_count(session, "drifted") == 3
        with test_db.session() as session:
            assert session.get(Event, "drifted").submission_count == 3

    def test_expire_all_does_not_lose_the_event(self, test_db, repos) -> None:
        # bump_content_version 里的 expire_all 若用得不对，取回的 event 会失效
        _, _, event_repo, _ = repos
        with test_db.session() as session:
            _make_event(session, "e1")
            assert event_repo.bump_content_version(session, "e1") == 1
            assert event_repo.get(session, "e1").content_version == 1


class TestPortsAreSatisfiable:
    """端口要能被测试替身实现——否则"可替换"只是说法。"""

    def test_rate_limiter_protocol(self) -> None:
        class FakeLimiter:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int, int]] = []

            def hit(self, key: str, *, limit: int, window_seconds: int):
                self.calls.append((key, limit, window_seconds))
                return RateLimitDecision(allowed=True, remaining=limit - 1)

            def reset(self) -> None:
                self.calls.clear()

        limiter = FakeLimiter()
        assert isinstance(limiter, RateLimiter)
        decision = limiter.hit("submit:ip:1.2.3.4", limit=5, window_seconds=60)
        assert decision.allowed is True and decision.remaining == 4

    def test_file_storage_protocol(self) -> None:
        class FakeStorage:
            def save(self, event_id, *, kind, original_name, stream):
                return StoredFile(stored_rel=f"{kind}/x.bin", size_bytes=1, sha256="s")

            def open(self, event_id, stored_rel):  # pragma: no cover
                raise NotImplementedError

            def exists(self, event_id, stored_rel):
                return True

            def delete(self, event_id, stored_rel) -> None:
                return None

            def size(self, event_id, stored_rel) -> int:
                return 1

        assert isinstance(FakeStorage(), FileStorage)

    def test_email_sender_protocol(self) -> None:
        import io

        sender = NullEmailSender()
        assert isinstance(sender, EmailSender)
        sender.send(to="a@example.com", subject="s", body="b")
        assert sender.sent == [("a@example.com", "s", "b")]

    def test_protocol_rejects_incomplete_double(self) -> None:
        # 反向验证：形状不对的替身不该被认为实现了端口
        class NotALimiter:
            pass

        assert not isinstance(NotALimiter(), RateLimiter)
