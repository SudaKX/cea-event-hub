"""任务 3.4：依赖注入的三种身份状态，以及请求级事务边界。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.clock import utcnow
from app.core.deps import AdminUser, CurrentEvent, CurrentUser, DbSession, RequiredUser
from app.core.enums import EventStatus, UserRole
from app.core.security import hash_password, hash_token
from app.db.models import Event, User, UserSession
from app.main import create_app

VALID_TOKEN = "valid-session-token"


def _seed_user(
    test_db,
    username: str = "alice",
    *,
    role: str = UserRole.USER.value,
    is_active: bool = True,
) -> int:
    with test_db.session() as session:
        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password("pw"),
            role=role,
            is_active=is_active,
        )
        session.add(user)
        session.flush()
        return user.id


def _issue_session(
    test_db,
    user_id: int,
    token: str = VALID_TOKEN,
    *,
    expires_in: int = 3600,
    revoked: bool = False,
) -> str:
    with test_db.session() as session:
        session.add(
            UserSession(
                token_hash=hash_token(token),
                user_id=user_id,
                expires_at=utcnow() + timedelta(seconds=expires_in),
                revoked_at=utcnow() if revoked else None,
            )
        )
    return token


def _seed_event(test_db, event_id: str = "spring-2026") -> None:
    with test_db.session() as session:
        session.add(
            Event(id=event_id, title="春季招新", status=EventStatus.LIVE.value)
        )


@pytest.fixture
def probe_client(test_db, monkeypatch):
    """带探针路由的应用，用来直接观察依赖解析结果。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_ENABLED", False)

    application = create_app()
    application.state.database = test_db

    @application.get("/probe/optional")
    def _optional(user: CurrentUser):
        return {"logged_in": user is not None, "id": user.id if user else None}

    @application.get("/probe/required")
    def _required(user: RequiredUser):
        return {"id": user.id, "role": user.role}

    @application.get("/probe/admin")
    def _admin(user: AdminUser):
        return {"id": user.id}

    @application.get("/probe/event/{event_id}")
    def _event(event: CurrentEvent):
        return {"id": event.id, "title": event.title}

    @application.post("/probe/write")
    def _write(session: DbSession):
        session.add(Event(id="probe-written", title="写入测试", status="draft"))
        return {"ok": True}

    @application.post("/probe/write-then-fail")
    def _write_then_fail(session: DbSession):
        session.add(Event(id="probe-ghost", title="不该留下", status="draft"))
        session.flush()
        raise RuntimeError("boom")

    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


class TestAnonymous:
    def test_optional_dependency_returns_none(self, probe_client) -> None:
        body = probe_client.get("/probe/optional").json()
        assert body == {"logged_in": False, "id": None}

    def test_required_dependency_is_401(self, probe_client) -> None:
        response = probe_client.get("/probe/required")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "login_required"

    def test_admin_dependency_is_401_not_403(self, probe_client) -> None:
        # 未登录应当是 401（先证明你是谁），而不是 403（你不够格）
        response = probe_client.get("/probe/admin")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "login_required"


class TestAuthenticated:
    def test_via_cookie(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db)
        token = _issue_session(test_db, user_id)

        probe_client.cookies.set("cea_sid", token)
        body = probe_client.get("/probe/optional").json()
        assert body == {"logged_in": True, "id": user_id}

        assert probe_client.get("/probe/required").status_code == 200

    def test_via_bearer_is_equivalent(self, probe_client, test_db) -> None:
        """两条认证路径必须完全等价，否则脚本与非浏览器客户端行为会不一致。"""
        user_id = _seed_user(test_db)
        token = _issue_session(test_db, user_id)

        via_bearer = probe_client.get(
            "/probe/required", headers={"Authorization": f"Bearer {token}"}
        )
        probe_client.cookies.set("cea_sid", token)
        via_cookie = probe_client.get("/probe/required")

        assert via_bearer.status_code == via_cookie.status_code == 200
        assert via_bearer.json() == via_cookie.json() == {
            "id": user_id,
            "role": UserRole.USER.value,
        }

    def test_bearer_is_case_insensitive_on_scheme(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db)
        token = _issue_session(test_db, user_id)
        response = probe_client.get(
            "/probe/required", headers={"Authorization": f"bEaReR {token}"}
        )
        assert response.status_code == 200

    def test_plain_user_is_forbidden_from_admin(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db, role=UserRole.USER.value)
        probe_client.cookies.set("cea_sid", _issue_session(test_db, user_id))

        response = probe_client.get("/probe/admin")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    def test_admin_passes_admin_guard(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db, "root", role=UserRole.ADMIN.value)
        probe_client.cookies.set("cea_sid", _issue_session(test_db, user_id))
        assert probe_client.get("/probe/admin").status_code == 200

    def test_cookie_takes_precedence_over_bearer(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db)
        _issue_session(test_db, user_id, "cookie-token")
        probe_client.cookies.set("cea_sid", "cookie-token")

        response = probe_client.get(
            "/probe/required", headers={"Authorization": "Bearer nonsense"}
        )
        assert response.status_code == 200


class TestSessionInvalidation:
    """会话校验必须每次都跑，且要覆盖账号启用状态。"""

    def test_expired_session_is_rejected(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db)
        token = _issue_session(test_db, user_id, expires_in=-1)
        probe_client.cookies.set("cea_sid", token)

        assert probe_client.get("/probe/required").status_code == 401
        assert probe_client.get("/probe/optional").json()["logged_in"] is False

    def test_revoked_session_is_rejected(self, probe_client, test_db) -> None:
        user_id = _seed_user(test_db)
        token = _issue_session(test_db, user_id, revoked=True)
        probe_client.cookies.set("cea_sid", token)

        assert probe_client.get("/probe/required").status_code == 401

    def test_disabled_account_loses_session_immediately(
        self, probe_client, test_db
    ) -> None:
        """停用必须立即生效，而不是等会话自然过期。"""
        user_id = _seed_user(test_db, is_active=False)
        token = _issue_session(test_db, user_id)
        probe_client.cookies.set("cea_sid", token)

        assert probe_client.get("/probe/required").status_code == 401
        assert probe_client.get("/probe/optional").json()["logged_in"] is False

    def test_unknown_token_is_rejected(self, probe_client, test_db) -> None:
        _seed_user(test_db)
        probe_client.cookies.set("cea_sid", "never-issued")
        assert probe_client.get("/probe/required").status_code == 401

    def test_empty_bearer_is_ignored(self, probe_client) -> None:
        response = probe_client.get(
            "/probe/optional", headers={"Authorization": "Bearer   "}
        )
        assert response.json()["logged_in"] is False

    def test_non_bearer_scheme_is_ignored(self, probe_client) -> None:
        response = probe_client.get(
            "/probe/optional", headers={"Authorization": "Basic dXNlcjpwdw=="}
        )
        assert response.json()["logged_in"] is False


class TestEventDependency:
    def test_existing_event_is_resolved(self, probe_client, test_db) -> None:
        _seed_event(test_db, "spring-2026")
        body = probe_client.get("/probe/event/spring-2026").json()
        assert body == {"id": "spring-2026", "title": "春季招新"}

    def test_missing_event_is_404(self, probe_client) -> None:
        response = probe_client.get("/probe/event/nope")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestRequestScopedTransaction:
    """一次请求 = 一个事务；这让"CAS 与插入同事务"不需要 service 额外编排。"""

    def test_write_is_committed(self, probe_client, test_db) -> None:
        assert probe_client.post("/probe/write").status_code == 200
        with test_db.session() as session:
            assert session.get(Event, "probe-written") is not None

    def test_failure_rolls_back_the_whole_request(self, probe_client, test_db) -> None:
        assert probe_client.post("/probe/write-then-fail").status_code == 500
        with test_db.session() as session:
            assert session.get(Event, "probe-ghost") is None, (
                "请求失败后写入仍然落库了——事务边界没有生效"
            )
