"""任务 1.2：配置的默认值与环境变量覆盖。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import _DEV_IP_HASH_SALT, Settings


class TestDefaults:
    def test_api_prefix_and_paths(self, make_settings) -> None:
        s = make_settings()
        assert s.API_PREFIX == "/api/v1"
        assert s.APP_ENV == "development"
        assert s.CONTENT_DIR.name == "content"
        assert s.DATA_DIR.name == "data"

    def test_submission_limits(self, make_settings) -> None:
        s = make_settings()
        assert s.MAX_PAYLOAD_BYTES == 64 * 1024
        assert s.MAX_UPLOAD_BYTES == 20 * 1024 * 1024
        assert s.MAX_FILES_PER_REQUEST == 10
        assert s.MAX_REQUEST_BYTES == 64 * 1024 * 1024

    def test_quota_defaults_match_design(self, make_settings) -> None:
        # design.md 决策 9：可匿名提交默认 4096 条，需登录默认不限额
        s = make_settings()
        assert s.MAX_SUBMISSIONS_PER_EVENT_ANON == 4096
        assert s.MAX_SUBMISSIONS_PER_EVENT_AUTHED is None

    def test_mail_and_recovery_flags_default_off(self, make_settings) -> None:
        # design.md 决策 12：三个开关默认关闭，等邮件接上再逐一打开
        s = make_settings()
        assert s.REQUIRE_EMAIL_VERIFICATION is False
        assert s.ALLOW_SELF_SERVICE_RESET is False
        assert s.EMAIL_BACKEND == "console"

    def test_admin_bootstrap_defaults(self, make_settings) -> None:
        # design.md 决策 18
        s = make_settings()
        assert s.ADMIN_BOOTSTRAP_ENABLED is True
        assert s.ADMIN_USERNAME == "admin"
        assert s.ADMIN_INITIAL_PASSWORD == ""

    def test_rate_limit_defaults(self, make_settings) -> None:
        s = make_settings()
        assert s.RATE_LIMIT_ENABLED is True
        assert s.RATE_LIMIT_SUBMIT_IP_MAX == 30
        assert s.RATE_LIMIT_AUTH_IP_MAX == 10
        assert s.TRUSTED_PROXY_IPS == ["127.0.0.1", "::1"]

    def test_reserved_usernames_are_lowercase(self, make_settings) -> None:
        # 归一化后比较，所以清单本身必须全小写，否则保留名形同虚设
        s = make_settings()
        assert "admin" in s.RESERVED_USERNAMES
        assert all(name == name.lower() for name in s.RESERVED_USERNAMES)

    def test_session_cookie_is_hardened_by_default(self, make_settings) -> None:
        s = make_settings()
        assert s.SESSION_COOKIE_SECURE is True
        assert s.SESSION_COOKIE_SAMESITE == "lax"


class TestEnvOverride:
    def test_env_vars_win(self, clean_env, monkeypatch) -> None:
        monkeypatch.setenv("API_PREFIX", "/api/v2")
        monkeypatch.setenv("MAX_SUBMISSIONS_PER_EVENT_ANON", "128")
        monkeypatch.setenv("ADMIN_USERNAME", "chief")
        monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "s3cret-from-env")
        monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
        monkeypatch.setenv("ALLOW_SELF_SERVICE_RESET", "true")
        monkeypatch.setenv("EMAIL_BACKEND", "smtp")
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

        s = Settings(_env_file=None)  # type: ignore[call-arg]

        assert s.API_PREFIX == "/api/v2"
        assert s.MAX_SUBMISSIONS_PER_EVENT_ANON == 128
        assert s.ADMIN_USERNAME == "chief"
        assert s.ADMIN_INITIAL_PASSWORD == "s3cret-from-env"
        assert s.REQUIRE_EMAIL_VERIFICATION is True
        assert s.ALLOW_SELF_SERVICE_RESET is True
        assert s.EMAIL_BACKEND == "smtp"
        assert s.SESSION_COOKIE_SECURE is False

    def test_unlimited_quota_via_empty_string(self, clean_env, monkeypatch) -> None:
        # None 值需要能通过环境变量表达为"不限额"
        monkeypatch.setenv("MAX_SUBMISSIONS_PER_EVENT_ANON", "")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.MAX_SUBMISSIONS_PER_EVENT_ANON is None


class TestValidation:
    def test_api_prefix_must_start_with_slash(self, make_settings) -> None:
        with pytest.raises(ValidationError):
            make_settings(API_PREFIX="api/v1")

    def test_api_prefix_must_not_end_with_slash(self, make_settings) -> None:
        with pytest.raises(ValidationError):
            make_settings(API_PREFIX="/api/v1/")

    def test_production_rejects_default_ip_salt(self, make_settings) -> None:
        # 未加盐的 IPv4 哈希可在数秒内暴力反查，生产必须显式提供盐
        with pytest.raises(ValidationError, match="IP_HASH_SALT"):
            make_settings(APP_ENV="production", IP_HASH_SALT=_DEV_IP_HASH_SALT)

    def test_production_accepts_explicit_ip_salt(self, make_settings) -> None:
        s = make_settings(APP_ENV="production", IP_HASH_SALT="a-real-secret")
        assert s.is_production is True

    def test_development_tolerates_dev_salt(self, make_settings) -> None:
        s = make_settings(APP_ENV="development", IP_HASH_SALT=_DEV_IP_HASH_SALT)
        assert s.is_production is False
