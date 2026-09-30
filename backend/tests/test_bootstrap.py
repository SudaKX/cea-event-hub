"""任务 2.5：首次启动引导管理员。"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import UserRole
from app.core.security import hash_password, verify_password
from app.db.models import User
from app.db.session import Database
from app.services.bootstrap import (
    SKIP_CONCURRENT,
    SKIP_DISABLED,
    SKIP_USERS_EXIST,
    ensure_bootstrap_admin,
    log_bootstrap_outcome,
)


def _count_users(database: Database) -> int:
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(User)) or 0


def _get(database: Database, username: str) -> User | None:
    with database.session() as session:
        return session.scalar(select(User).where(User.username == username))


def _seed_plain_user(database: Database, username: str = "alice") -> None:
    with database.session() as session:
        session.add(
            User(
                username=username,
                display_name=username,
                password_hash=hash_password("whatever"),
                role=UserRole.USER.value,
            )
        )


class TestCreation:
    def test_empty_database_creates_admin(self, test_db, make_settings) -> None:
        outcome = ensure_bootstrap_admin(
            test_db, make_settings(ADMIN_INITIAL_PASSWORD="chosen-password")
        )

        assert outcome.created is True
        assert outcome.username == "admin"

        user = _get(test_db, "admin")
        assert user is not None
        assert user.role == UserRole.ADMIN.value
        assert user.is_active is True
        assert verify_password("chosen-password", user.password_hash)

    def test_random_password_is_usable(self, test_db, make_settings) -> None:
        outcome = ensure_bootstrap_admin(test_db, make_settings())

        assert outcome.generated_password
        user = _get(test_db, "admin")
        assert user is not None
        assert verify_password(outcome.generated_password, user.password_hash)

    def test_username_is_normalized(self, test_db, make_settings) -> None:
        outcome = ensure_bootstrap_admin(
            test_db, make_settings(ADMIN_USERNAME="  Chief  ", ADMIN_INITIAL_PASSWORD="p")
        )
        assert outcome.username == "chief"
        assert _get(test_db, "chief") is not None


class TestTriggerCondition:
    def test_any_existing_user_blocks_bootstrap(self, test_db, make_settings) -> None:
        """触发器是"用户表为空"，而不是"不存在名为 admin 的账号"。

        否则管理员被有意删除后，一次重启就会悄悄复活一个账号名已知的特权账号。
        """
        _seed_plain_user(test_db, "alice")  # 故意不叫 admin

        outcome = ensure_bootstrap_admin(test_db, make_settings())

        assert outcome.created is False
        assert outcome.skipped_reason == SKIP_USERS_EXIST
        assert _get(test_db, "admin") is None
        assert _count_users(test_db) == 1

    def test_disabled_switch_creates_nothing(self, test_db, make_settings) -> None:
        outcome = ensure_bootstrap_admin(
            test_db, make_settings(ADMIN_BOOTSTRAP_ENABLED=False)
        )

        assert outcome.created is False
        assert outcome.skipped_reason == SKIP_DISABLED
        assert _count_users(test_db) == 0

    def test_repeated_startup_does_not_duplicate(self, test_db, make_settings) -> None:
        settings = make_settings(ADMIN_INITIAL_PASSWORD="p")

        first = ensure_bootstrap_admin(test_db, settings)
        second = ensure_bootstrap_admin(test_db, settings)

        assert first.created is True
        assert second.created is False
        assert second.skipped_reason == SKIP_USERS_EXIST
        assert _count_users(test_db) == 1

    def test_concurrent_startup_is_tolerated(
        self, test_db, make_settings, monkeypatch
    ) -> None:
        """两个进程可能都读到空表，此时唯一约束才是权威。"""
        _seed_plain_user(test_db, "admin")

        # 强制计数检查返回 0，制造"另一个进程已经抢先建好"的时序
        monkeypatch.setattr(Session, "scalar", lambda self, *a, **k: 0)

        outcome = ensure_bootstrap_admin(test_db, make_settings())

        assert outcome.created is False
        assert outcome.skipped_reason == SKIP_CONCURRENT

        # 必须先撤销补丁：下面的计数同样走 Session.scalar
        monkeypatch.undo()
        assert _count_users(test_db) == 1


class TestPasswordOutput:
    def test_random_password_is_logged_once(
        self, test_db, make_settings, caplog
    ) -> None:
        settings = make_settings()

        with caplog.at_level(logging.WARNING):
            outcome = ensure_bootstrap_admin(test_db, settings)
        assert outcome.generated_password is not None
        assert outcome.generated_password in caplog.text
        assert "立即" in caplog.text  # 提示改密

        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            again = ensure_bootstrap_admin(test_db, settings)
        assert again.created is False
        assert outcome.generated_password not in caplog.text, "口令在重启后又被输出了"

    def test_provided_password_is_never_logged(
        self, test_db, make_settings, caplog
    ) -> None:
        secret = "env-supplied-secret-value"

        with caplog.at_level(logging.DEBUG):
            outcome = ensure_bootstrap_admin(
                test_db, make_settings(ADMIN_INITIAL_PASSWORD=secret)
            )
            log_bootstrap_outcome(outcome)

        assert outcome.created is True
        assert outcome.generated_password is None
        assert secret not in caplog.text, "环境变量提供的口令不应出现在任何输出中"
        # 但仍要说明账号已建好
        assert "admin" in caplog.text

    def test_provided_password_logs_creation_at_info(
        self, test_db, make_settings, caplog
    ) -> None:
        with caplog.at_level(logging.INFO):
            outcome = ensure_bootstrap_admin(
                test_db, make_settings(ADMIN_INITIAL_PASSWORD="x")
            )
            log_bootstrap_outcome(outcome)
        # 口令来自环境变量时不需要告警级别：没有需要人工保存的秘密
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestStartupWiring:
    def test_app_startup_creates_admin_in_injected_database(
        self, app, test_db, monkeypatch
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_ENABLED", True)
        monkeypatch.setattr(settings, "ADMIN_USERNAME", "admin")
        monkeypatch.setattr(settings, "ADMIN_INITIAL_PASSWORD", "startup-password")

        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200

        user = _get(test_db, "admin")
        assert user is not None
        assert user.role == UserRole.ADMIN.value
        assert verify_password("startup-password", user.password_hash)

    def test_startup_skips_when_disabled(self, app, test_db, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_ENABLED", False)

        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200

        assert _count_users(test_db) == 0
