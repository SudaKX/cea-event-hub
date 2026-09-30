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
def app(test_db: Database):
    """应用实例指向临时库。

    启动任务（首次引导、后续的 janitor）读的是 `app.state.database`，
    因此注入后不会碰开发库 `var/app.db`。
    """
    application = create_app()
    application.state.database = test_db
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client

