"""共享测试夹具。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import Database
from app.main import create_app


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
    return application


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
def admin_client(client, admin_id: int):
    """已登录管理员的客户端。"""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def user_client(client, test_db: Database):
    """已登录普通用户的客户端。"""
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "correct-horse"},
    )
    assert response.status_code == 201, response.text
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "correct-horse"},
        ).status_code
        == 200
    )
    return client


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

