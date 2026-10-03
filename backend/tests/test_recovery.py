"""任务 5.1 - 5.6：邮件端口、一次性令牌、找回双路径、邮箱验证。"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.config import settings as global_settings
from app.core.enums import TokenPurpose, UserRole
from app.core.security import hash_password, hash_token
from app.db.models import User, UserToken
from app.infra import build_email_sender
from app.infra.email_console import ConsoleEmailSender
from app.services.tokens import UserTokenService

API = "/api/v1"
TOKEN_IN_BODY = re.compile(r"token=([A-Za-z0-9_\-]+)")


def _extract_token(body: str) -> str:
    match = TOKEN_IN_BODY.search(body)
    assert match, f"邮件正文里没有找到令牌链接：\n{body}"
    return match.group(1)


@pytest.fixture
def mailbox(app) -> ConsoleEmailSender:
    """把应用的邮件后端换成可检查的实例。"""
    sender = ConsoleEmailSender()
    app.state.email_sender = sender
    return sender


def _register(client, username="alice", password="correct-horse", **extra):
    """只发注册的**第一步**。

    要造出真实账号请用 `register` 夹具。这里保留单步，是因为这一组关心的是邮箱
    绑定与找回，而不是注册本身的两阶段。

    默认邮箱由**归一化后**的用户名拼出来 —— 拿原始值拼会生出带空格的非法地址。
    """
    return client.post(
        f"{API}/auth/register",
        json={
            "username": username,
            "password": password,
            "email": extra.pop("email", f"{username.strip().lower()}@example.com"),
            **extra,
        },
    )


def _latest_token(sender, to: str) -> str:
    """取**最近**一封发给该地址的邮件里的令牌。

    注册本身也会发一封信，所以 `sent[0]` 早就不再是"这一组测试关心的那封"了 ——
    取最后一封才对应刚刚发起的那个动作。
    """
    for recipient, _subject, body in reversed(sender.sent):
        if recipient == to:
            return _extract_token(body)
    raise AssertionError(f"没有发给 {to} 的邮件：{sender.sent}")


def _login(client, username="alice", password="correct-horse"):
    return client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )


def _seed_admin(test_db, username="root") -> int:
    with test_db.session() as session:
        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password("admin-pass-123"),
            role=UserRole.ADMIN.value,
        )
        session.add(user)
        session.flush()
        return user.id


def _seed_user_with_email(test_db, email="alice@example.com") -> int:
    with test_db.session() as session:
        user = User(
            username="alice",
            display_name="Alice",
            password_hash=hash_password("correct-horse"),
            email=email,
            role=UserRole.USER.value,
        )
        session.add(user)
        session.flush()
        return user.id


class TestConsoleEmailSender:
    """任务 5.1"""

    def test_records_and_does_not_raise(self, caplog) -> None:
        import logging

        sender = ConsoleEmailSender()
        with caplog.at_level(logging.INFO):
            sender.send(to="a@example.com", subject="主题", body="正文内容")

        assert sender.sent == [("a@example.com", "主题", "正文内容")]
        assert "a@example.com" in caplog.text
        assert "正文内容" in caplog.text

    def test_build_falls_back_to_console_when_smtp_unconfigured(
        self, make_settings, caplog
    ) -> None:
        """邮件不可用时必须降级而不是报错，否则注册与找回会一起坏掉。"""
        import logging

        settings = make_settings(EMAIL_BACKEND="smtp", SMTP_HOST="")
        with caplog.at_level(logging.WARNING):
            sender = build_email_sender(settings)

        assert isinstance(sender, ConsoleEmailSender)
        assert "SMTP_HOST" in caplog.text

    def test_build_selects_smtp_when_configured(self, make_settings) -> None:
        from app.infra.email_smtp import SmtpEmailSender

        sender = build_email_sender(
            make_settings(EMAIL_BACKEND="smtp", SMTP_HOST="smtp.example.com")
        )
        assert isinstance(sender, SmtpEmailSender)


class TestUserTokenService:
    """任务 5.2"""

    def test_issue_stores_only_a_digest(self, test_db, make_settings) -> None:
        user_id = _seed_user_with_email(test_db)
        service = UserTokenService(make_settings())

        with test_db.session() as session:
            user = session.get(User, user_id)
            token = service.issue(
                session, user=user, purpose=TokenPurpose.PASSWORD_RESET
            )

        with test_db.session() as session:
            record = session.scalar(select(UserToken))
        assert record is not None
        assert record.token_hash == hash_token(token)
        assert record.token_hash != token
        assert token not in record.token_hash

    def test_consume_marks_used(self, test_db, make_settings) -> None:
        user_id = _seed_user_with_email(test_db)
        service = UserTokenService(make_settings())

        with test_db.session() as session:
            token = service.issue(
                session, user=session.get(User, user_id),
                purpose=TokenPurpose.PASSWORD_RESET,
            )
        with test_db.session() as session:
            record = service.consume(
                session, token=token, purpose=TokenPurpose.PASSWORD_RESET
            )
            assert record.used_at is not None

    def test_consume_twice_is_rejected(self, test_db, make_settings) -> None:
        from app.core.exceptions import TokenInvalid

        user_id = _seed_user_with_email(test_db)
        service = UserTokenService(make_settings())

        with test_db.session() as session:
            token = service.issue(
                session, user=session.get(User, user_id),
                purpose=TokenPurpose.PASSWORD_RESET,
            )
        with test_db.session() as session:
            service.consume(session, token=token, purpose=TokenPurpose.PASSWORD_RESET)

        with pytest.raises(TokenInvalid):
            with test_db.session() as session:
                service.consume(
                    session, token=token, purpose=TokenPurpose.PASSWORD_RESET
                )

    def test_expired_token_is_rejected(self, test_db, make_settings) -> None:
        from app.core.exceptions import TokenInvalid

        user_id = _seed_user_with_email(test_db)
        service = UserTokenService(make_settings())

        with test_db.session() as session:
            token = service.issue(
                session, user=session.get(User, user_id),
                purpose=TokenPurpose.PASSWORD_RESET,
            )
        with test_db.session() as session:
            record = session.scalar(select(UserToken))
            assert record is not None
            record.expires_at = utcnow() - timedelta(seconds=1)

        with pytest.raises(TokenInvalid):
            with test_db.session() as session:
                service.consume(
                    session, token=token, purpose=TokenPurpose.PASSWORD_RESET
                )

    def test_purpose_mismatch_is_rejected(self, test_db, make_settings) -> None:
        from app.core.exceptions import TokenInvalid

        user_id = _seed_user_with_email(test_db)
        service = UserTokenService(make_settings())

        with test_db.session() as session:
            token = service.issue(
                session, user=session.get(User, user_id),
                purpose=TokenPurpose.EMAIL_VERIFY,
            )

        with pytest.raises(TokenInvalid):
            with test_db.session() as session:
                service.consume(
                    session, token=token, purpose=TokenPurpose.PASSWORD_RESET
                )

    def test_issuing_again_invalidates_the_previous_one(
        self, test_db, make_settings
    ) -> None:
        """"点了两次忘记密码"不该留下两条都能用的链接。"""
        from app.core.exceptions import TokenInvalid

        user_id = _seed_user_with_email(test_db)
        service = UserTokenService(make_settings())

        with test_db.session() as session:
            first = service.issue(
                session, user=session.get(User, user_id),
                purpose=TokenPurpose.PASSWORD_RESET,
            )
        with test_db.session() as session:
            service.issue(
                session, user=session.get(User, user_id),
                purpose=TokenPurpose.PASSWORD_RESET,
            )

        with pytest.raises(TokenInvalid):
            with test_db.session() as session:
                service.consume(
                    session, token=first, purpose=TokenPurpose.PASSWORD_RESET
                )

    def test_unknown_token_is_rejected(self, test_db, make_settings) -> None:
        from app.core.exceptions import TokenInvalid

        service = UserTokenService(make_settings())
        with pytest.raises(TokenInvalid):
            with test_db.session() as session:
                service.consume(
                    session, token="never-issued", purpose=TokenPurpose.PASSWORD_RESET
                )


class TestSelfServiceReset:
    """任务 5.3 / 5.4：自助路径。"""

    def test_disabled_by_default(self, client, test_db, mailbox) -> None:
        _seed_user_with_email(test_db)
        response = client.post(
            f"{API}/auth/forgot-password", json={"email": "alice@example.com"}
        )
        assert response.status_code == 204
        assert mailbox.sent == []

    def test_sends_a_link_when_enabled(
        self, client, test_db, mailbox, monkeypatch
    ) -> None:
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)

        response = client.post(
            f"{API}/auth/forgot-password", json={"email": "alice@example.com"}
        )
        assert response.status_code == 204
        assert len(mailbox.sent) == 1
        assert mailbox.sent[0][0] == "alice@example.com"

    def test_unknown_email_is_indistinguishable(
        self, client, test_db, mailbox, monkeypatch
    ) -> None:
        """区分"已注册"与"未注册"等于免费提供一个账号枚举接口。"""
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)

        known = client.post(
            f"{API}/auth/forgot-password", json={"email": "alice@example.com"}
        )
        unknown = client.post(
            f"{API}/auth/forgot-password", json={"email": "nobody@example.com"}
        )

        assert known.status_code == unknown.status_code == 204
        assert known.content == unknown.content == b""
        # 未注册的邮箱不实际发信
        assert len(mailbox.sent) == 1

    def test_full_reset_flow(self, client, test_db, mailbox, monkeypatch) -> None:
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)

        client.post(f"{API}/auth/forgot-password", json={"email": "alice@example.com"})
        token = _extract_token(mailbox.sent[0][2])

        response = client.post(
            f"{API}/auth/reset",
            json={"token": token, "new_password": "fresh-password-1"},
        )
        assert response.status_code == 204

        assert _login(client, "alice", "correct-horse").status_code == 401
        assert _login(client, "alice", "fresh-password-1").status_code == 200

    def test_token_cannot_be_reused(
        self, client, test_db, mailbox, monkeypatch
    ) -> None:
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)

        client.post(f"{API}/auth/forgot-password", json={"email": "alice@example.com"})
        token = _extract_token(mailbox.sent[0][2])

        assert (
            client.post(
                f"{API}/auth/reset",
                json={"token": token, "new_password": "fresh-password-1"},
            ).status_code
            == 204
        )
        second = client.post(
            f"{API}/auth/reset",
            json={"token": token, "new_password": "another-password-2"},
        )
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "token_invalid"

    def test_unknown_token_is_rejected(self, client) -> None:
        response = client.post(
            f"{API}/auth/reset",
            json={"token": "nope", "new_password": "fresh-password-1"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "token_invalid"

    def test_expired_token_is_rejected(
        self, client, test_db, mailbox, monkeypatch
    ) -> None:
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)

        client.post(f"{API}/auth/forgot-password", json={"email": "alice@example.com"})
        token = _extract_token(mailbox.sent[0][2])

        with test_db.session() as session:
            record = session.scalar(select(UserToken))
            assert record is not None
            record.expires_at = utcnow() - timedelta(seconds=1)

        response = client.post(
            f"{API}/auth/reset",
            json={"token": token, "new_password": "fresh-password-1"},
        )
        assert response.status_code == 400

    def test_short_new_password_is_rejected(
        self, client, test_db, mailbox, monkeypatch
    ) -> None:
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)
        client.post(f"{API}/auth/forgot-password", json={"email": "alice@example.com"})
        token = _extract_token(mailbox.sent[0][2])

        response = client.post(
            f"{API}/auth/reset", json={"token": token, "new_password": "tiny"}
        )
        assert response.status_code == 422
        assert "new_password" in response.json()["error"]["fields"]

    def test_reset_revokes_existing_sessions(
        self, client, test_db, mailbox, monkeypatch
    ) -> None:
        """"我怀疑账号被人用过"正是这个流程的触发场景。"""
        monkeypatch.setattr(global_settings, "ALLOW_SELF_SERVICE_RESET", True)
        _seed_user_with_email(test_db)

        assert _login(client).status_code == 200
        assert client.get(f"{API}/auth/me").status_code == 200

        client.post(f"{API}/auth/forgot-password", json={"email": "alice@example.com"})
        token = _extract_token(mailbox.sent[0][2])
        client.post(
            f"{API}/auth/reset",
            json={"token": token, "new_password": "fresh-password-1"},
        )

        assert client.get(f"{API}/auth/me").status_code == 401


class TestAdminIssuedReset:
    """任务 5.4 / 5.6：管理员签发路径，与自助路径共用兑换端点。"""

    def test_admin_issues_and_user_redeems(self, client, test_db) -> None:
        _seed_admin(test_db)
        target_id = _seed_user_with_email(test_db)

        assert _login(client, "root", "admin-pass-123").status_code == 200
        response = client.post(f"{API}/admin/users/{target_id}/reset-token")
        assert response.status_code == 200

        body = response.json()
        assert body["username"] == "alice"
        token = body["token"]

        # 用户（未登录）用这个令牌重置 —— 与自助路径是同一个端点
        client.cookies.clear()
        assert (
            client.post(
                f"{API}/auth/reset",
                json={"token": token, "new_password": "from-admin-123"},
            ).status_code
            == 204
        )
        assert _login(client, "alice", "from-admin-123").status_code == 200

    def test_plaintext_never_returned_again(self, client, test_db) -> None:
        """明文只在签发响应里出现一次；库里只有摘要。"""
        _seed_admin(test_db)
        target_id = _seed_user_with_email(test_db)
        _login(client, "root", "admin-pass-123")

        token = client.post(f"{API}/admin/users/{target_id}/reset-token").json()["token"]

        # 再次签发拿到的是不同的明文
        again = client.post(f"{API}/admin/users/{target_id}/reset-token").json()["token"]
        assert again != token

        # 数据库里任何地方都不存在该明文
        with test_db.session() as session:
            records = session.scalars(select(UserToken)).all()
        assert all(record.token_hash != token for record in records)
        assert all(token not in record.token_hash for record in records)

    def test_requires_admin(self, client, test_db, register) -> None:
        target_id = _seed_user_with_email(test_db)

        # 匿名
        assert client.post(f"{API}/admin/users/{target_id}/reset-token").status_code == 401

        # 普通用户 —— 走完两阶段，否则 bob 还不存在，登录会失败
        assert register(client, username="bob", password="bob-password").status_code == 204
        assert _login(client, "bob", "bob-password").status_code == 200
        assert client.post(f"{API}/admin/users/{target_id}/reset-token").status_code == 403

    def test_unknown_user_is_404(self, client, test_db) -> None:
        _seed_admin(test_db)
        _login(client, "root", "admin-pass-123")
        assert client.post(f"{API}/admin/users/9999/reset-token").status_code == 404

    def test_disabled_user_cannot_receive_a_token(self, client, test_db) -> None:
        _seed_admin(test_db)
        target_id = _seed_user_with_email(test_db)
        with test_db.session() as session:
            session.get(User, target_id).is_active = False

        _login(client, "root", "admin-pass-123")
        response = client.post(f"{API}/admin/users/{target_id}/reset-token")
        assert response.status_code == 400


class TestEmailVerification:
    """任务 5.5"""

    def test_email_uniqueness_is_case_insensitive(self, client, test_db) -> None:
        # 走完两阶段，让 alice 成为**真实账号**；否则后面撞上的是占位冲突，
        # 那是另一个错误码（registration_pending）
        assert _register(client, "alice", email="A@Example.com").status_code == 202
        response = _register(client, "bob", email="a@example.com")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "registration_pending"

    def test_unverified_email_does_not_block_login(self, client, test_db) -> None:
        """验证开关默认关闭，且绝不允许默认拦住登录。

        这里**直接种一个未验证的账号**：两阶段注册必然把邮箱标记为已验证，所以
        "未验证"这个状态只能由种子数据造出来 —— 而它正是本用例要覆盖的。
        """
        _seed_user_with_email(test_db, "alice@example.com")

        assert _login(client, "alice").status_code == 200

        me = client.get(f"{API}/auth/me").json()["user"]
        assert me["email"] == "alice@example.com"
        assert me["email_verified"] is False

    def test_verification_flow(self, client, test_db, mailbox) -> None:
        """用**种子**的未验证账号。

        两阶段注册本身就会把邮箱标记为已验证，所以"未验证"这个状态只剩种子数据能
        造 —— 而 `/auth/verify-email` 这条流程恰恰只对那种状态有意义。
        """
        _seed_user_with_email(test_db, "alice@example.com")
        assert _login(client, "alice").status_code == 200

        assert client.post(f"{API}/auth/verify-email/request").status_code == 204
        token = _latest_token(mailbox, "alice@example.com")

        assert (
            client.post(f"{API}/auth/verify-email", json={"token": token}).status_code
            == 204
        )
        assert client.get(f"{API}/auth/me").json()["user"]["email_verified"] is True

    def test_verification_token_is_single_use(self, client, test_db, mailbox) -> None:
        _seed_user_with_email(test_db, "alice@example.com")
        assert _login(client, "alice").status_code == 200

        assert client.post(f"{API}/auth/verify-email/request").status_code == 204
        token = _latest_token(mailbox, "alice@example.com")

        assert (
            client.post(f"{API}/auth/verify-email", json={"token": token}).status_code
            == 204
        )
        second = client.post(f"{API}/auth/verify-email", json={"token": token})
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "token_invalid"

    def test_request_without_email_is_rejected(self, client, test_db) -> None:
        # 两阶段注册的邮箱是必填的，所以"没有邮箱"只能由种子数据造出来
        _seed_user_with_email(test_db, "alice@example.com")
        with test_db.session() as session:
            session.scalar(select(User).where(User.username == "alice")).email = None

        _login(client, "alice")
        response = client.post(f"{API}/auth/verify-email/request")
        assert response.status_code == 422
        assert "email" in response.json()["error"]["fields"]

    def test_request_requires_login(self, client) -> None:
        assert client.post(f"{API}/auth/verify-email/request").status_code == 401
