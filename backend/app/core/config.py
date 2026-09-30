"""应用配置。

所有可调参数集中在这里，可由环境变量或 backend/.env 覆盖。
设计约束：阈值与开关一律来自配置，代码里不出现魔法数字。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/ -> 仓库根
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

# 开发环境用的固定盐。生产环境必须显式提供 IP_HASH_SALT，否则启动失败。
_DEV_IP_HASH_SALT = "dev-only-salt-not-for-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ---------- 基础 ----------
    APP_ENV: str = "development"
    APP_NAME: str = "CEA Event Hub"
    # nginx 按此前缀把请求导到后端
    API_PREFIX: str = "/api/v1"
    # 用于生成找回链接等对外地址
    PUBLIC_BASE_URL: str = "http://localhost:5173"
    # 承载活动页的宿主来源，供桥接层与 CORS 判定使用
    APP_ORIGIN: str = "http://localhost:5173"

    # ---------- 路径 ----------
    DATABASE_URL: str = f"sqlite+pysqlite:///{(REPO_ROOT / 'var' / 'app.db').as_posix()}"
    CONTENT_DIR: Path = REPO_ROOT / "content"
    DATA_DIR: Path = REPO_ROOT / "data"

    # ---------- 会话 ----------
    SESSION_TTL_SECONDS: int = 7 * 24 * 3600
    SESSION_COOKIE_NAME: str = "cea_sid"
    # 生产必须为 True；开发若走 http 需在 .env 关闭
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_COOKIE_PATH: str = "/"

    # ---------- 安全 ----------
    # 未加盐的 IP 哈希可在数秒内暴力反查，因此生产必须提供盐
    IP_HASH_SALT: str = _DEV_IP_HASH_SALT

    # ---------- 提交策略 ----------
    # 新建活动时 submission_requires_login 的**种子值**。它只在创建那一刻参与，
    # 之后运行时唯一真源是活动自己的字段——单层来源，避免两处配置打架
    SUBMISSION_REQUIRES_LOGIN_SEED: bool = False

    # ---------- 提交体积与数量约束 ----------
    MAX_PAYLOAD_BYTES: int = 64 * 1024
    MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024
    MAX_FILES_PER_REQUEST: int = 10
    MAX_REQUEST_BYTES: int = 64 * 1024 * 1024
    # 可匿名提交活动的默认条数上限；需登录活动默认不限额
    MAX_SUBMISSIONS_PER_EVENT_ANON: int | None = 4096
    MAX_SUBMISSIONS_PER_EVENT_AUTHED: int | None = None
    # 同一提交者提交完全相同内容的去重窗口
    DEDUP_WINDOW_SECONDS: int = 300
    # 单活动数据目录字节配额
    MAX_EVENT_STORAGE_BYTES: int = 512 * 1024 * 1024

    # ---------- 内容包投放 ----------
    MAX_CONTENT_ARCHIVE_BYTES: int = 32 * 1024 * 1024
    MAX_CONTENT_ENTRIES: int = 2000
    MAX_CONTENT_EXTRACTED_BYTES: int = 200 * 1024 * 1024

    # ---------- 限流（应用层内存实现）----------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_SUBMIT_IP_MAX: int = 30
    RATE_LIMIT_SUBMIT_IP_WINDOW: int = 60
    RATE_LIMIT_SUBMIT_USER_MAX: int = 20
    RATE_LIMIT_SUBMIT_USER_WINDOW: int = 60
    RATE_LIMIT_AUTH_IP_MAX: int = 10
    RATE_LIMIT_AUTH_IP_WINDOW: int = 60
    # 仅信任这些来源写入的转发头；其余请求的 X-Forwarded-For 一律忽略
    TRUSTED_PROXY_IPS: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])

    # ---------- 邮件 ----------
    EMAIL_BACKEND: str = "console"  # console | smtp
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    EMAIL_FROM: str = "noreply@example.com"

    # ---------- 找回与验证 ----------
    EMAIL_TOKEN_TTL_SECONDS: int = 24 * 3600
    # 三个默认关闭的开关，等邮件接上再逐一打开
    REQUIRE_EMAIL_VERIFICATION: bool = False
    ALLOW_SELF_SERVICE_RESET: bool = False
    REGISTRATION_INVITE_CODE: str = ""

    # ---------- 首次启动引导管理员 ----------
    ADMIN_BOOTSTRAP_ENABLED: bool = True
    ADMIN_USERNAME: str = "admin"
    ADMIN_INITIAL_PASSWORD: str = ""

    # ---------- 用户名保留名（归一化后比较，故一律小写）----------
    RESERVED_USERNAMES: list[str] = Field(
        default_factory=lambda: [
            "admin", "administrator", "root", "system", "sysadmin", "superuser",
            "moderator", "mod", "staff", "official", "owner", "president",
            "cea", "ceaadmin", "guest", "anonymous", "null", "undefined",
            "api", "www", "mail", "smtp", "support", "help", "security",
            "test", "demo", "example",
        ]
    )

    # ---------- 后台清理 ----------
    JANITOR_ENABLED: bool = True
    JANITOR_INTERVAL_SECONDS: int = 300
    # 超过此时长仍未完成的落盘记录视为残留
    PENDING_FILE_TTL_SECONDS: int = 3600

    # ---------- 内容缓存 ----------
    CONTENT_CORS_ALLOW_ORIGIN: str = "*"

    @field_validator(
        "MAX_SUBMISSIONS_PER_EVENT_ANON",
        "MAX_SUBMISSIONS_PER_EVENT_AUTHED",
        mode="before",
    )
    @classmethod
    def _empty_means_unlimited(cls, value: object) -> object:
        """允许用空值表达"不限额"。

        可匿名活动的默认上限是 4096，运维若想放开某个部署的匿名配额，需要一个
        显式的"无上限"写法；环境变量里 `MAX_SUBMISSIONS_PER_EVENT_ANON=` 是最
        自然的表达，因此空串归一化为 None，而不是让 int 解析报错。
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        prefix = self.API_PREFIX
        if not prefix.startswith("/"):
            raise ValueError("API_PREFIX 必须以 / 开头")
        if prefix != "/" and prefix.endswith("/"):
            raise ValueError("API_PREFIX 不能以 / 结尾")

        if self.APP_ENV == "production" and self.IP_HASH_SALT == _DEV_IP_HASH_SALT:
            raise ValueError(
                "生产环境必须显式设置 IP_HASH_SALT：未加盐的 IP 哈希可在数秒内被暴力反查，"
                "等于明文存储客户端地址"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    def ensure_directories(self) -> None:
        """确保运行时目录存在。"""
        for path in (self.CONTENT_DIR, self.DATA_DIR, REPO_ROOT / "var"):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
