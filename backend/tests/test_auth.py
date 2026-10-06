"""任务 4.1 - 4.6：安全原语、注册、登录与会话、修改口令。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

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
from app.db.models import PendingRegistration, User, UserSession
from conftest import invitation_code_for
from tests.conftest import verification_token

API = "/api/v1"


def _start_registration(client, username="alice", password="correct-horse", **extra):
    """只发**第一步**（建立占位），返回 202 那个响应。

    要造出真正的用户请用 `register` 夹具 —— 它把两步都走完。这里保留单步，是为了
    让"第一步本身的行为"（校验、冲突、重入）能被直接观察。

    默认邮箱由**归一化后**的用户名拼出来：拿原始值拼的话，`"  Alice  "` 会生出
    `"  Alice  @example.com"` 这种非法地址，测试就会因为一个无关的理由失败。
    """
    return client.post(
        f"{API}/auth/register",
        json={
            # 注册现在必须持码；这个辅助函数只走第一步，所以自己造一张
            "invitation_code": invitation_code_for(client),
            "username": username,
            "password": password,
            "email": extra.pop("email", f"{username.strip().lower()}@example.com"),
            **extra,
        },
    )


def _login(client, username="alice", password="correct-horse"):
    return client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )


@pytest.fixture
def registered(client, register):
    """一个已通过邮箱验证的真实账号。"""
    assert register(client).status_code == 204
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
    """任务 4.2 与 16.2–16.3：两阶段注册。"""

    def test_request_creates_no_account(self, client, test_db) -> None:
        """**这是两阶段的核心断言。**

        请求只建立占位。先建账号再验证邮箱会给每一个写错邮箱的注册留下一个永远
        无法验证的账号 —— 它占着用户名、可能被用来登录、还得靠人工清理。
        """
        response = _start_registration(client)
        assert response.status_code == 202
        assert response.json()["ongoing"] is False

        with test_db.session() as session:
            assert session.scalar(select(User).where(User.username == "alice")) is None
            assert session.scalar(select(PendingRegistration)) is not None

    def test_request_sends_a_verification_link(self, client, sent_emails) -> None:
        _start_registration(client)
        recipient, subject, body = sent_emails.sent[-1]
        assert recipient == "alice@example.com"
        assert "完成注册" in subject
        assert "/verify-registration?token=" in body

    def test_the_email_carries_an_html_version_too(self, client, sent_emails) -> None:
        """纯文本与 HTML 两个版本都要发。

        只发其中一边都有代价：只发 HTML 会被反垃圾系统扣分、纯文本客户端看到一片
        空白；只发纯文本则等于这套排版白做。链接**两边都要有** —— 它才是这封信的
        用处所在。
        """
        _start_registration(client)
        html = sent_emails.htmls[-1]
        assert html is not None
        assert "<html" in html
        assert "verify-registration?token=" in html

    def test_the_idempotency_key_changes_with_the_message(
        self, client, test_db, sent_emails
    ) -> None:
        """幂等键必须唯一对应**那一条消息**，而不是"那一行"。

        这里踩过一次：键曾经从占位行的自增 id 派生，而 `pending_registrations.id`
        是 rowid 别名、**没有 AUTOINCREMENT** —— 一行被删掉（过期清理、或核销）之后，
        下一行会**拿回同一个 id**。于是两封内容不同的邮件撞上同一个键，Resend 判定为
        "改了内容的重放"直接拒掉：

            This idempotency key has been used ... but the request body was modified

        表现是注册接口照常返回 202、而这封信**永远发不出去**。所以键要从**消息本身**
        取（这里用令牌摘要：每一次签发都是一条新消息），不能从行 id 取。
        """
        assert _start_registration(client).status_code == 202
        first_key = sent_emails.keys[-1]
        first_token = verification_token(sent_emails, "alice@example.com")

        # 让占位消失：模拟过期后被清理（核销也是一样，行同样会没）
        with test_db.session() as session:
            pending = session.scalar(select(PendingRegistration))
            assert pending is not None
            session.delete(pending)

        assert _start_registration(client).status_code == 202
        second_key = sent_emails.keys[-1]
        second_token = verification_token(sent_emails, "alice@example.com")

        # 两条消息确实不同（令牌是新签发的）
        assert second_token != first_token
        # ……那么键也必须不同。相同就意味着第二封信会被服务端拒收
        assert second_key != first_key, "幂等键不能随行 id 复用而重复"

    def test_password_is_never_stored_in_clear(self, client, test_db) -> None:
        _start_registration(client, password="correct-horse")
        with test_db.session() as session:
            pending = session.scalar(select(PendingRegistration))
        assert pending is not None
        assert "correct-horse" not in pending.password_hash
        assert pending.password_hash.startswith("$argon2")

    def test_username_is_normalized_on_the_pending_row(self, client, test_db) -> None:
        assert _start_registration(client, username="  Alice  ").status_code == 202
        with test_db.session() as session:
            assert session.scalar(
                select(PendingRegistration).where(
                    PendingRegistration.username == "alice"
                )
            )

    def test_username_uniqueness_is_case_insensitive(self, client) -> None:
        assert _start_registration(client, username="Alice").status_code == 202

        duplicate = _start_registration(client, username="alice", email="other@example.com")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "registration_pending"

    def test_pending_conflict_is_distinct_from_taken(self, client, register) -> None:
        """**两种冲突必须分得清。**

        "已被注册"要用户换个名字或去登录；"有待验证的注册"要他去查收邮件。合并成
        一句会把第二种情形里的人送去一个根本不存在账号的登录页。
        """
        assert register(client, username="bob", email="bob@example.com").status_code == 204

        # 真实账号占用 → username_taken
        taken = _start_registration(client, username="bob", email="b2@example.com")
        assert taken.status_code == 409
        assert taken.json()["error"]["code"] == "username_taken"

        # 另一条占位占用 → registration_pending，且指出是哪个字段
        _start_registration(client, username="carol", email="carol@example.com")
        pending = _start_registration(client, username="carol", email="c2@example.com")
        assert pending.status_code == 409
        assert pending.json()["error"]["code"] == "registration_pending"
        assert "username" in pending.json()["error"]["fields"]

    def test_same_pair_is_a_reentry(self, client, sent_emails) -> None:
        """同一对重入不新建、**不重发**。

        重发会让用户收到两封一模一样的邮件，而其中的旧链接仍然有效 —— 那才是真正
        让人困惑的地方。
        """
        assert _start_registration(client).status_code == 202
        before = len(sent_emails.sent)

        again = _start_registration(client)
        assert again.status_code == 202
        assert again.json()["ongoing"] is True
        assert len(sent_emails.sent) == before

    def test_expired_pending_frees_the_slot_on_the_next_request(
        self, client, test_db
    ) -> None:
        """到期即刻释放 —— 不必等后台清理任务跑。

        唯一索引**不认时间**：过期占位在被真正删除前会一直占着用户名与邮箱。
        """
        _start_registration(client)
        with test_db.session() as session:
            pending = session.scalar(select(PendingRegistration))
            assert pending is not None
            pending.expires_at = utcnow() - timedelta(seconds=1)

        assert _start_registration(client).status_code == 202

    @pytest.mark.parametrize(
        "reserved", ["admin", "ADMIN", "root", "System", "administrator"]
    )
    def test_reserved_usernames_are_rejected(self, client, reserved: str) -> None:
        """保留名不可移除：引导账号名可预测，防线之一就是没人能抢注它。"""
        response = _start_registration(client, username=reserved)
        assert response.status_code == 422
        assert "username" in response.json()["error"]["fields"]

    @pytest.mark.parametrize("bad", ["ab", "has space", "bad!char", "UPPER!"])
    def test_invalid_username_shapes_are_rejected(self, client, bad: str) -> None:
        response = _start_registration(client, username=bad)
        assert response.status_code == 422
        assert "username" in response.json()["error"]["fields"]

    def test_short_password_is_rejected(self, client) -> None:
        response = _start_registration(client, password="short")
        assert response.status_code == 422
        assert "password" in response.json()["error"]["fields"]

    def test_invalid_email_shape_is_rejected(self, client) -> None:
        response = _start_registration(client, email="not-an-email")
        assert response.status_code == 422
        assert "email" in response.json()["error"]["fields"]

    def test_email_is_required(self, client) -> None:
        # 没有"仅凭用户名注册"的降级路径：验证的对象就是邮箱
        response = client.post(
            f"{API}/auth/register",
            json={"username": "alice", "password": "correct-horse"},
        )
        assert response.status_code == 422

    def test_email_uniqueness_is_case_insensitive(self, client) -> None:
        assert _start_registration(client, "alice", email="A@Example.com").status_code == 202
        response = _start_registration(client, "bob", email="a@example.com")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "registration_pending"


class TestRegistrationVerification:
    """任务 16.3：核销。"""

    def test_verify_creates_the_account(self, client, test_db, sent_emails) -> None:
        _start_registration(client)

        response = client.post(
            f"{API}/auth/register/verify",
            json={"token": verification_token(sent_emails, "alice@example.com")},
        )
        assert response.status_code == 204

        with test_db.session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
            # 占位被消费掉了
            assert session.scalar(select(PendingRegistration)) is None
        assert user is not None
        assert user.role == UserRole.USER.value
        assert user.is_active is True
        # 邮箱刚刚被证明是可达的 —— 这正是本流程的产出
        assert user.email_verified_at is not None
        assert user.email == "alice@example.com"

    def test_the_new_account_can_log_in_with_the_pending_password(
        self, client, sent_emails
    ) -> None:
        _start_registration(client, password="correct-horse")
        client.post(
            f"{API}/auth/register/verify",
            json={"token": verification_token(sent_emails, "alice@example.com")},
        )
        assert _login(client).status_code == 200

    def test_link_is_single_use(self, client, sent_emails, test_db) -> None:
        _start_registration(client)
        token = verification_token(sent_emails, "alice@example.com")

        assert client.post(
            f"{API}/auth/register/verify", json={"token": token}
        ).status_code == 204
        assert client.post(
            f"{API}/auth/register/verify", json={"token": token}
        ).status_code == 400

        with test_db.session() as session:
            count = session.scalar(select(func.count()).select_from(User))
        assert count == 1

    def test_expired_link_is_rejected(self, client, sent_emails, test_db) -> None:
        _start_registration(client)
        with test_db.session() as session:
            pending = session.scalar(select(PendingRegistration))
            assert pending is not None
            pending.expires_at = utcnow() - timedelta(seconds=1)

        response = client.post(
            f"{API}/auth/register/verify",
            json={"token": verification_token(sent_emails, "alice@example.com")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "token_invalid"

    def test_unknown_token_is_rejected(self, client) -> None:
        response = client.post(
            f"{API}/auth/register/verify", json={"token": generate_token()}
        )
        # 与"过期"同一个错误：不把"这个凭据存在但过期了"泄露出去
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "token_invalid"

    def test_verify_does_not_issue_a_session(self, client, sent_emails) -> None:
        # "注册"与"获得会话"是两件明确的事
        _start_registration(client)
        client.post(
            f"{API}/auth/register/verify",
            json={"token": verification_token(sent_emails, "alice@example.com")},
        )
        assert client.get(f"{API}/auth/me").status_code == 401

    def test_conflict_leaves_the_pending_row_intact(
        self, client, sent_emails, test_db
    ) -> None:
        """核销时撞上真人抢注，**占位必须还在**。

        核销与建号同事务，失败即整体回滚。否则用户会既没建成账号、又丢了凭据 ——
        连重试的机会都没有。

        抢注只能**直接插一行**来模拟：走接口的话，占位已经锁住了那个用户名，
        第二次注册会在第一步就被挡下 —— 那正是"冲突在提交那一刻就暴露"的意思。
        """
        _start_registration(client, username="alice")
        token = verification_token(sent_emails, "alice@example.com")

        with test_db.session() as session:
            session.add(
                User(
                    username="alice",
                    display_name="抢注者",
                    password_hash=hash_password("another-pass"),
                    email="other@example.com",
                    role=UserRole.USER.value,
                )
            )

        response = client.post(f"{API}/auth/register/verify", json={"token": token})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "registration_conflict"

        with test_db.session() as session:
            assert session.scalar(select(PendingRegistration)) is not None

    def test_verification_token_is_not_stored_in_clear(
        self, client, sent_emails, test_db
    ) -> None:
        _start_registration(client)
        token = verification_token(sent_emails, "alice@example.com")
        with test_db.session() as session:
            pending = session.scalar(select(PendingRegistration))
        assert pending is not None and pending.token_hash != token


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
