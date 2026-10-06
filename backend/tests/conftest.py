"""共享测试夹具。"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

# ---------------------------------------------------------------------------
# **必须在导入 app 之前**：`app.main` 在模块级就 `create_app()`，而它会按
# `EMAIL_BACKEND` 装配发信后端。开发者的 `.env` 里若写着 `resend`，那边就会因为
# 没有密钥而拒绝启动 —— 于是整个测试套件在收集阶段就挂掉。
#
# 测试不该依赖开发者本机的邮件配置。这里强制回到 console，由 `app` 夹具再换成
# 可捕获的替身。
# ---------------------------------------------------------------------------
os.environ["EMAIL_BACKEND"] = "console"
os.environ.pop("RESEND_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.ports import NullEmailSender  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """清掉所有 Settings 字段对应的环境变量。

    否则开发者本机若导出过 API_PREFIX 之类的变量，"默认值" 断言就会随机失败。
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def make_settings(clean_env: pytest.MonkeyPatch):
    """构造不读取 .env 的 Settings，使测试与本地配置完全隔离。"""

    def _make(**overrides: object) -> Settings:
        return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def test_db(tmp_path) -> Iterator[Database]:
    """每个测试一个独立的临时 SQLite 文件库。

    用文件而不是 :memory:，因为内存库在并发连接下行为与真实部署差异过大。
    """
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def db_session(test_db: Database) -> Iterator:
    with test_db.session() as session:
        yield session


@pytest.fixture
def content_root(tmp_path) -> "Path":
    """每个测试独立的内容/数据根目录。

    不隔离的话，测试会把活动内容写进仓库里的 content/ 与 data/。
    """
    from pathlib import Path

    root = Path(tmp_path)
    (root / "content").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def app(test_db: Database, monkeypatch: pytest.MonkeyPatch, content_root):
    """应用实例指向临时库与临时内容目录。

    启动任务（首次引导、后续的 janitor）读的是 `app.state.database`，
    因此注入后不会碰开发库 `var/app.db`。

    这里同时关掉首次引导，否则每个用到客户端的测试都会生成一个随机管理员
    口令并以告警级别打出来，把测试输出淹掉。需要引导的测试自行打开开关。
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_ENABLED", False)
    # 测试走 http，Secure Cookie 不会被回传，因此按开发环境的配置来
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", False)
    # 内容与数据目录指向临时位置（StaticFiles 在 create_app 时绑定目录）
    monkeypatch.setattr(settings, "CONTENT_DIR", content_root / "content")
    monkeypatch.setattr(settings, "DATA_DIR", content_root / "data")

    application = create_app()
    application.state.database = test_db
    # 换成可捕获的发信实现：注册现在是两阶段的，测试必须能从邮件里取出验证 token。
    # 顺带把 console 实现往 stdout 打印的邮件正文挡在测试输出之外。
    application.state.email_sender = NullEmailSender()
    return application


@pytest.fixture
def sent_emails(app) -> NullEmailSender:
    """本次测试发出的邮件。`sent` 里是 (收件人, 主题, 正文)。"""
    return app.state.email_sender


def verification_token(sender: NullEmailSender, to: str) -> str:
    """从最近一封发给该地址的邮件里取出验证 token。

    正则从正文里捞 —— 链接的形状由服务层拼装，这里只认 `token=` 那一段。
    """
    for recipient, _subject, body in reversed(sender.sent):
        if recipient != to:
            continue
        match = re.search(r"[?&]token=([A-Za-z0-9_\-]+)", body)
        assert match, f"邮件里没有验证链接：{body}"
        return match.group(1)
    raise AssertionError(f"没有发给 {to} 的邮件：{sender.sent}")


def make_invitation_code(
    database, *, max_uses: int = 1, days: int = 7, token: str | None = None
) -> str:
    """造一张可用的邀请码，返回它的 token。

    **注册现在必须持码**，因此凡是要走到注册第一步的测试都需要一张。这是模块级
    函数而不是夹具，因为有一批测试用**模块级辅助函数**直接打注册端点（它们要观察
    第一步本身的行为，不走走完两步的夹具），而那些辅助函数拿不到夹具。

    直接走仓储而不是管理端接口：这些用例要的是"有一张能用的码"，不是"管理员能建码"
    （后者由 `test_invitations.py` 覆盖）。
    """
    from datetime import timedelta

    from app.core.clock import utcnow
    from app.core.security import generate_invitation_token
    from app.db.models import InvitationCode
    from app.repositories.invitations import InvitationRepository

    value = token or generate_invitation_token()
    with database.session() as session:
        InvitationRepository().add(
            session,
            InvitationCode(
                token=value,
                name="测试用",
                owner_id=None,
                max_uses=max_uses,
                used_count=0,
                expires_at=utcnow() + timedelta(days=days),
            ),
        )
    return value


def invitation_code_for(client, **kwargs: object) -> str:
    """给某个测试客户端造一张码。

    从**它自己的 app** 取库，因此模块级辅助函数（拿不到夹具的那些）也能用。这是
    `make_invitation_code` 的客户端版本，存在的理由只有一个：让补码这件事在调用点
    写成一行。
    """
    return make_invitation_code(client.app.state.database, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def invitation_code(test_db) -> str:
    """夹具形态：`invitation_code()` 每次给一张新的码。"""

    def _make(*, max_uses: int = 1, days: int = 7, token: str | None = None) -> str:
        return make_invitation_code(
            test_db, max_uses=max_uses, days=days, token=token
        )

    return _make


@pytest.fixture
def register(client, sent_emails, invitation_code):
    """走完两阶段注册，返回**最后一步**（核销）的响应。

    注册不再是"一次请求建号"，因此凡是要造出一个真实用户的测试都得走两步。把它
    收成一个夹具，改动就集中在这里，而不是散落到每个用例里。

    **邀请码由夹具自动补一张**（除非调用方自己传 `invitation_code=`）。这样既有用例
    一处都不用改，而邀请码本身的用例仍能指定具体的那张（包括传一个无效的）。
    """

    def _register(
        target_client,
        *,
        username: str = "alice",
        password: str = "correct-horse",
        email: str | None = None,
        invitation_code: str | None = None,
        **extra: object,
    ):
        address = email or f"{username.strip().lower()}@example.com"
        started = target_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": password,
                "email": address,
                "invitation_code": invitation_code or _fresh_code(),
                **extra,
            },
        )
        if started.status_code != 202:
            return started
        return target_client.post(
            "/api/v1/auth/register/verify",
            json={"token": verification_token(sent_emails, address)},
        )

    def _fresh_code() -> str:
        # 每次注册用一张**新的**码：一次一用的码在第二次注册时会失败，而夹具的语义
        # 是"造出一个用户"，不该被邀请码的次数绊住
        return invitation_code()

    return _register


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


ADMIN_USERNAME = "root"
ADMIN_PASSWORD = "admin-pass-123"


@pytest.fixture
def admin_id(test_db: Database) -> int:
    """直接在库里造一个管理员，绕过注册（注册只能产生普通用户）。"""
    from app.core.enums import UserRole
    from app.core.security import hash_password
    from app.db.models import User

    with test_db.session() as session:
        user = User(
            username=ADMIN_USERNAME,
            display_name=ADMIN_USERNAME,
            password_hash=hash_password(ADMIN_PASSWORD),
            role=UserRole.ADMIN.value,
        )
        session.add(user)
        session.flush()
        return user.id


@pytest.fixture
def admin_client(app, admin_id: int):
    """已登录管理员的客户端。

    **每个身份一个独立的 TestClient。** 共用一个实例的话，各夹具的登录会互相
    覆盖 Cookie——表现为"管理员突然变成普通用户"，而且症状取决于夹具的执行
    顺序，极难排查。
    """
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture
def user_client(app, test_db: Database, register):
    """已登录普通用户的客户端（独立实例，见 admin_client 的说明）。

    走完整的两阶段注册 —— 注册不再是一次请求建号。
    """
    with TestClient(app) as test_client:
        response = register(test_client, username="alice")
        assert response.status_code == 204, response.text
        assert (
            test_client.post(
                "/api/v1/auth/login",
                json={"username": "alice", "password": "correct-horse"},
            ).status_code
            == 200
        )
        yield test_client


@pytest.fixture
def anon_client(app):
    """**另一个**浏览器：与已登录客户端共享 app，但不共享 Cookie。

    需要同时观察"匿名"与"已登录"两种身份时必须用它——`client` 与
    `user_client` 是同一个 TestClient 实例，Cookie 是共享的。
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fault_client(app):
    """不把未捕获异常抛回测试的客户端。

    用来验证"服务端炸了"这条路径本身（例如落库失败后必须清理已写文件）。
    默认的 TestClient 会把异常直接抛进测试，那样就观察不到 500 响应。
    """
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

