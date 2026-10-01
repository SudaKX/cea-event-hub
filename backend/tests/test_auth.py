"""任务 4.1 - 4.6：安全原语、注册、登录与会话、修改口令。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.config import settings as global_settings
from app.core.enums import UserRole
from app.core.security import (
    canonical_json,
    generate_password,
    generate_token,
    hash_ip,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from app.db.models import User, UserSession

API = "/api/v1"


def _register(client, username="alice", password="correct-horse", **extra):
    return client.post(
        f"{API}/auth/register",
        json={"username": username, "password": password, **extra},
    )


def _login(client, username="alice", password="correct-horse"):
    return client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )


@pytest.fixture
def registered(client):
    assert _register(client).status_code == 201
    return "alice"


class TestSecurityPrimitives:
    """任务 4.1"""

    def test_password_hash_round_trip(self) -> None:
        stored = hash_password("s3cret-pass")
        assert stored != "s3cret-pass"
        assert stored.startswith("$argon2")
        assert verify_password("s3cret-pass", stored) is True
        assert verify_password("wrong", stored) is False

    def test_same_password_yields_different_hash(self) -> None:
        # 加盐：两个用户用同一个口令，库里也不能出现相同的哈希
        assert hash_password("same") != hash_password("same")

    def test_invalid_hash_is_rejected_not_raised(self) -> None:
        assert verify_password("x", "not-a-hash") is False

    def test_needs_rehash_reports_bad_hash(self) -> None:
        assert needs_rehash("garbage") is True
        assert needs_rehash(hash_password("x")) is False

    def test_token_is_high_entropy_and_unique(self) -> None:
        tokens = {generate_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(t) >= 32 for t in tokens)

    def test_token_digest_is_irreversible(self) -> None:
        token = "raw-session-token"
        digest = hash_token(token)
        assert digest != token
        assert len(digest) == 64  # sha256 hex
        # 同一个输入稳定，不同输入不同
        assert hash_token(token) == digest
        assert hash_token("other") != digest

    def test_generated_password_is_strong_and_unique(self) -> None:
        passwords = {generate_password() for _ in range(50)}
        assert len(passwords) == 50
        assert all(len(p) >= 16 for p in passwords)

    def test_ip_hash_is_salted(self) -> None:
        # 不加盐的 IPv4 哈希几秒就能暴力反查，等于明文存地址
        assert hash_ip("1.2.3.4", "salt-a") != hash_ip("1.2.3.4", "salt-b")
        assert hash_ip("1.2.3.4", "salt-a") == hash_ip("1.2.3.4", "salt-a")
        assert len(hash_ip("1.2.3.4", "salt-a")) == 64

    def test_canonical_json_is_key_order_independent(self) -> None:
        # 同内容必须得到同样的字节数，否则按体积上限的校验会随键序抖动
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
        assert len(canonical_json({"a": 1, "b": 2})) == len(
            canonical_json({"b": 2, "a": 1})
        )
        assert canonical_json({"a": 1}) != canonical_json({"a": 2})

    def test_canonical_json_keeps_unicode_readable(self) -> None:
        assert "张三" in canonical_json({"name": "张三"})

    def test_password_hash_differs_from_token_digest_path(self) -> None:
        """两者是刻意的不同算法：口令慢、令牌快。"""
        password_hash = hash_password("abc")
        assert password_hash != hash_token("abc")
        assert len(password_hash) > 64


class TestRegistration:
    """任务 4.2"""

    def test_register_creates_plain_active_user(self, client, test_db) -> None:
        response = _register(client)
        assert response.status_code == 201

        body = response.json()["user"]
        assert body["username"] == "alice"
        assert body["role"] == UserRole.USER.value
        assert body["email_verified"] is False

        with test_db.session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
        assert user is not None and user.is_active is True

    def test_response_never_contains_password_hash(self, client) -> None:
        text = _register(client).text
        assert "password" not in text
        assert "argon2" not in text

    def test_username_is_normalized_on_write(self, client, test_db) -> None:
        assert _register(client, username="  Alice  ").status_code == 201
        with test_db.session() as session:
            # 存的是归一化值，唯一约束才在两库上行为一致
            assert session.scalar(select(User).where(User.username == "alice"))

    def test_username_uniqueness_is_case_insensitive(self, client) -> None:
        assert _register(client, username="Alice").status_code == 201

        duplicate = _register(client, username="alice")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "username_taken"

    @pytest.mark.parametrize(
        "reserved", ["admin", "ADMIN", "root", "System", "administrator"]
    )
    def test_reserved_usernames_are_rejected(self, client, reserved: str) -> None:
        """保留名不可移除：引导账号名可预测，防线之一就是没人能抢注它。"""
        response = _register(client, username=reserved)
        assert response.status_code == 422
        assert "username" in response.json()["error"]["fields"]

    def test_reserved_list_is_compared_after_normalization(
        self, client, test_db
    ) -> None:
        assert _register(client, username="  AdMiN  ").status_code == 422
        with test_db.session() as session:
            assert session.scalar(select(User).where(User.username == "admin")) is None

    @pytest.mark.parametrize("bad", ["ab", "has space", "bad!char", "UPPER!"])
    def test_invalid_username_shapes_are_rejected(self, client, bad: str) -> None:
        response = _register(client, username=bad)
        assert response.status_code == 422
        assert "username" in response.json()["error"]["fields"]

    def test_short_password_is_rejected(self, client) -> None:
        response = _register(client, password="short")
        assert response.status_code == 422
        assert "password" in response.json()["error"]["fields"]

    def test_invalid_email_shape_is_rejected(self, client) -> None:
        response = _register(client, email="not-an-email")
        assert response.status_code == 422
        assert "email" in response.json()["error"]["fields"]

    def test_email_uniqueness_is_case_insensitive(self, client) -> None:
        assert _register(client, "alice", email="A@Example.com").status_code == 201
        response = _register(client, "bob", email="a@example.com")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "email_taken"

    def test_register_does_not_issue_a_session(self, client) -> None:
        # "注册"与"获得会话"是两件明确的事
        assert _register(client).status_code == 201
        assert client.get(f"{API}/auth/me").status_code == 401

    def test_display_name_defaults_to_username(self, client, test_db) -> None:
        _register(client)
        with test_db.session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
        assert user is not None and user.display_name == "alice"


class TestInviteCode:
    def test_disabled_by_default(self, client) -> None:
        assert _register(client).status_code == 201

    def test_wrong_code_is_rejected(self, client, monkeypatch) -> None:
        monkeypatch.setattr(global_settings, "REGISTRATION_INVITE_CODE", "let-me-in")
        response = _register(client, invite_code="nope")
        assert response.status_code == 422
        assert "invite_code" in response.json()["error"]["fields"]

    def test_missing_code_is_rejected_when_enabled(self, client, monkeypatch) -> None:
        monkeypatch.setattr(global_settings, "REGISTRATION_INVITE_CODE", "let-me-in")
        assert _register(client).status_code == 422

    def test_correct_code_is_accepted(self, client, monkeypatch) -> None:
        monkeypatch.setattr(global_settings, "REGISTRATION_INVITE_CODE", "let-me-in")
        assert _register(client, invite_code="let-me-in").status_code == 201


class TestLoginAndSession:
    """任务 4.3"""

    def test_login_sets_httponly_cookie(self, client, registered) -> None:
        response = _login(client)
        assert response.status_code == 200

        set_cookie = response.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/" in set_cookie

    def test_secure_flag_follows_configuration(self, client, registered, monkeypatch) -> None:
        monkeypatch.setattr(global_settings, "SESSION_COOKIE_SECURE", True)
        set_cookie = _login(client).headers["set-cookie"]
        assert "Secure" in set_cookie

    def test_response_body_contains_no_credential(self, client, registered) -> None:
        response = _login(client)
        token = client.cookies.get(global_settings.SESSION_COOKIE_NAME)

        assert token
        assert token not in response.text, "会话凭据出现在了响应体里"
        assert "token" not in response.text.lower()
        assert response.json()["user"]["username"] == "alice"

    def test_me_returns_current_user(self, client, registered) -> None:
        _login(client)
        response = client.get(f"{API}/auth/me")
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "alice"

    def test_me_requires_login(self, client) -> None:
        response = client.get(f"{API}/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "login_required"

    def test_bearer_path_is_equivalent(self, client, registered) -> None:
        _login(client)
        token = client.cookies.get(global_settings.SESSION_COOKIE_NAME)

        client.cookies.clear()
        response = client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "alice"

    def test_logout_revokes_session_server_side(self, client, registered) -> None:
        _login(client)
        token = client.cookies.get(global_settings.SESSION_COOKIE_NAME)

        assert client.post(f"{API}/auth/logout").status_code == 204
        assert client.get(f"{API}/auth/me").status_code == 401

        # 服务端已删除该会话，而不是仅清了浏览器的 Cookie
        client.cookies.set(global_settings.SESSION_COOKIE_NAME, token)
        assert client.get(f"{API}/auth/me").status_code == 401

    def test_logout_without_session_is_harmless(self, client) -> None:
        assert client.post(f"{API}/auth/logout").status_code == 204

    def test_session_row_stores_a_digest(self, client, registered, test_db) -> None:
        _login(client)
        token = client.cookies.get(global_settings.SESSION_COOKIE_NAME)

        with test_db.session() as session:
            record = session.scalar(select(UserSession))
        assert record is not None
        assert record.token_hash == hash_token(token)
        assert record.token_hash != token


class TestAntiEnumeration:
    """任务 4.5"""

    def test_unknown_user_and_wrong_password_are_indistinguishable(
        self, client, registered
    ) -> None:
        wrong_password = _login(client, "alice", "definitely-wrong")
        unknown_user = _login(client, "nobody", "definitely-wrong")

        assert wrong_password.status_code == unknown_user.status_code == 401
        assert wrong_password.json() == unknown_user.json()
        assert wrong_password.json()["error"]["code"] == "invalid_credentials"

    def test_unknown_user_still_pays_the_hashing_cost(
        self, client, registered
    ) -> None:
        """不等价耗时的响应本身就是账号枚举的信道。"""
        import time

        def elapsed(username: str) -> float:
            start = time.perf_counter()
            _login(client, username, "wrong-password")
            return time.perf_counter() - start

        # 先各跑一次预热（首次会构建 dummy hash）
        elapsed("alice")
        elapsed("nobody")

        known = min(elapsed("alice") for _ in range(3))
        unknown = min(elapsed("nobody") for _ in range(3))

        # 只要同一量级即可：没有哑哈希时这会相差一个数量级
        assert unknown > known / 5, (
            f"不存在账号的响应明显更快（{unknown:.4f}s vs {known:.4f}s），"
            "说明没有执行等价的口令校验"
        )

    def test_disabled_account_reports_clearly_after_password_check(
        self, client, test_db
    ) -> None:
        """只有本来就持有正确口令的人才会得知账号被停用——不构成枚举。"""
        with test_db.session() as session:
            session.add(
                User(
                    username="banned",
                    display_name="banned",
                    password_hash=hash_password("correct-horse"),
                    role=UserRole.USER.value,
                    is_active=False,
                )
            )

        response = _login(client, "banned", "correct-horse")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "account_disabled"

        # 口令错误时仍然是统一的 401，不泄露账号状态
        assert _login(client, "banned", "wrong").status_code == 401


class TestSessionLifecycle:
    """任务 4.4"""

    def test_expired_session_is_rejected(self, client, registered, test_db) -> None:
        _login(client)
        with test_db.session() as session:
            record = session.scalar(select(UserSession))
            assert record is not None
            record.expires_at = utcnow() - timedelta(seconds=1)

        assert client.get(f"{API}/auth/me").status_code == 401

    def test_disabling_account_invalidates_session_immediately(
        self, client, registered, test_db
    ) -> None:
        _login(client)
        assert client.get(f"{API}/auth/me").status_code == 200

        with test_db.session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
            assert user is not None
            user.is_active = False

        assert client.get(f"{API}/auth/me").status_code == 401

    def test_login_twice_creates_two_independent_sessions(
        self, client, registered, test_db
    ) -> None:
        _login(client)
        first = client.cookies.get(global_settings.SESSION_COOKIE_NAME)
        _login(client)
        second = client.cookies.get(global_settings.SESSION_COOKIE_NAME)

        assert first != second
        with test_db.session() as session:
            assert len(session.scalars(select(UserSession)).all()) == 2


class TestChangePassword:
    """任务 4.6"""

    def test_change_then_login_with_new_password(self, client, registered) -> None:
        _login(client)
        response = client.post(
            f"{API}/auth/password",
            json={"current_password": "correct-horse", "new_password": "brand-new-pass"},
        )
        assert response.status_code == 204

        client.cookies.clear()
        assert _login(client, "alice", "correct-horse").status_code == 401
        assert _login(client, "alice", "brand-new-pass").status_code == 200

    def test_wrong_current_password_is_rejected(self, client, registered) -> None:
        _login(client)
        response = client.post(
            f"{API}/auth/password",
            json={"current_password": "nope", "new_password": "brand-new-pass"},
        )
        assert response.status_code == 422
        assert "current_password" in response.json()["error"]["fields"]

        client.cookies.clear()
        assert _login(client, "alice", "correct-horse").status_code == 200

    def test_short_new_password_is_rejected(self, client, registered) -> None:
        _login(client)
        response = client.post(
            f"{API}/auth/password",
            json={"current_password": "correct-horse", "new_password": "tiny"},
        )
        assert response.status_code == 422
        assert "new_password" in response.json()["error"]["fields"]

    def test_same_password_is_rejected(self, client, registered) -> None:
        _login(client)
        response = client.post(
            f"{API}/auth/password",
            json={
                "current_password": "correct-horse",
                "new_password": "correct-horse",
            },
        )
        assert response.status_code == 422

    def test_other_sessions_are_revoked(self, client, app, registered) -> None:
        """改密通常意味着"我怀疑凭据泄露了"，因此其他会话必须立即失效。"""
        from fastapi.testclient import TestClient

        # 会话 A：发起改密的那个
        _login(client)
        token_a = client.cookies.get(global_settings.SESSION_COOKIE_NAME)

        with TestClient(app) as other:
            # 会话 B：同一用户的另一个"浏览器"，必须是**独立的一次登录**，
            # 否则两边共用同一个会话，也就无所谓"其他会话"了
            assert _login(other).status_code == 200
            token_b = other.cookies.get(global_settings.SESSION_COOKIE_NAME)
            assert token_b != token_a
            assert other.get(f"{API}/auth/me").status_code == 200

            response = client.post(
                f"{API}/auth/password",
                json={
                    "current_password": "correct-horse",
                    "new_password": "brand-new-pass",
                },
            )
            assert response.status_code == 204

            # 发起改密的会话保留，其他会话失效
            assert client.get(f"{API}/auth/me").status_code == 200
            assert other.get(f"{API}/auth/me").status_code == 401

    def test_requires_login(self, client, registered) -> None:
        response = client.post(
            f"{API}/auth/password",
            json={"current_password": "correct-horse", "new_password": "brand-new-pass"},
        )
        assert response.status_code == 401
