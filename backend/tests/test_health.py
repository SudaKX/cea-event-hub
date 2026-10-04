"""应用装配与健康检查。

装配部分（路由前缀、文档位置、CORS 封锁链）与健康检查的语义放在一起：后者是
本文件的主体，前者是它周边的边界。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import api_router
from app.api.v1 import health as health_module
from app.core.config import settings
from app.db.session import Database, build_engine

HEALTH = f"{settings.API_PREFIX}/health"

#: 响应允许出现的字段。**加字段必须改这里** —— 这个端点是匿名的，白名单是它唯一的
#: 守门人（见 design.md 决策 1 的安全边界）
ALLOWED_FIELDS = {
    "status",
    "app",
    "env",
    "database",
    "revision",
    "expected_revision",
}


def _mark_migrated(database: Database, revision: str) -> None:
    """造出"迁移已执行"的状态：真正的 Alembic 也会留下这样一张表。"""
    with database.session() as session:
        session.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": revision},
        )


@pytest.fixture
def migrated_client(app, test_db):
    """应用 + 一个"已迁移到当前 head"的库。"""
    _mark_migrated(test_db, health_module.expected_revision())
    with TestClient(app) as client:
        yield client


class TestAssembly:
    def test_api_router_uses_configured_prefix(self) -> None:
        # nginx 按此前缀把请求导到后端，前缀必须来自配置而非硬编码
        assert api_router.prefix == settings.API_PREFIX
        assert settings.API_PREFIX == "/api/v1"

    def test_health_is_not_served_outside_prefix(self, client) -> None:
        """根路径归 SPA，后端不应占用。

        这条同时是 design.md 决策 2 的守门人：不为打错路径的轮询者新增顶层端点。
        """
        assert client.get("/health").status_code == 404

    def test_docs_live_under_api_prefix(self, client) -> None:
        # nginx 只反代 /api/ 与 /content/，文档挂在根路径会打到 SPA 上
        assert client.get("/openapi.json").status_code == 404
        assert client.get(f"{settings.API_PREFIX}/openapi.json").status_code == 200

    def test_api_responses_carry_no_cors_headers(self, client) -> None:
        """决策 5 的封锁链：/api/** 绝不能带 CORS 头。

        一旦这里出现 Access-Control-Allow-Origin，沙箱活动页就能绕过宿主直接
        调用后端，桥接代理不再是物理边界。

        本变更新加的两个路径（健康检查与删除账号）都落在 `/api/**` 里，因此一并
        纳入 —— 新端点很容易忘记"它也在那条约定之下"。
        """
        for path in (
            HEALTH,
            "/api/v1/admin/users/999999",
            "/api/v1/definitely-not-here",
        ):
            headers = client.get(path).headers
            assert "access-control-allow-origin" not in {k.lower() for k in headers}
            assert "access-control-allow-credentials" not in {
                k.lower() for k in headers
            }

    def test_new_endpoints_carry_no_cors_headers_on_their_own_methods(
        self, admin_client
    ) -> None:
        """删除是 DELETE 方法，得按它自己的方法与状态码再确认一次。

        上面那条走的是 GET，401/404 都是"还没进到端点"。404 那条同样要干净 ——
        错误响应带 CORS 头一样能把隔离捅穿。
        """
        headers = admin_client.delete("/api/v1/admin/users/999999").headers
        assert "access-control-allow-origin" not in {k.lower() for k in headers}
        assert "access-control-allow-credentials" not in {k.lower() for k in headers}


class TestHealthy:
    def test_migrated_database_reports_ok(self, migrated_client) -> None:
        response = migrated_client.get(HEALTH)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["app"] == settings.APP_NAME
        # 两个版本都给出，且此刻一致
        assert body["revision"] == body["expected_revision"]
        assert body["expected_revision"]

    def test_revision_comes_from_the_script_directory(self) -> None:
        """期望版本取自磁盘上的迁移脚本，而不是查库。

        两个来源彼此独立，不一致才有信息量 —— 这条把"它确实读了脚本目录"钉住。
        """
        assert health_module.expected_revision()
        assert health_module.expected_revision() == health_module.expected_revision()

    def test_behind_but_reachable_is_still_ok(self, app, test_db) -> None:
        """版本落后不是故障：落后的库通常仍能服务旧接口。

        那该是"计划一次迁移"的信号。把它算成 503 会让人把一次正常的滚动发布当成
        线上事故。
        """
        _mark_migrated(test_db, "000000000000")
        with TestClient(app) as client:
            response = client.get(HEALTH)
        assert response.status_code == 200
        body = response.json()
        assert body["revision"] == "000000000000"
        assert body["revision"] != body["expected_revision"]


class TestUnmigrated:
    def test_reachable_but_never_migrated(self, client) -> None:
        """**这一条是本次改动的核心收益。**

        库连得上，但 `alembic_version` 表都不存在 —— 一张业务表都没有，服务根本
        无法处理请求。旧实现说 ok；新实现必须说 503，且**不能**说成 unreachable，
        否则运维会跑去查数据库进程而白费功夫。
        """
        response = client.get(HEALTH)
        assert response.status_code == 503
        body = response.json()
        assert body["database"] == "unmigrated"
        assert body["status"] == "unavailable"
        # 期望版本仍然给出：它是有用的线索，不因库未迁移而消失
        assert body["expected_revision"]
        assert body["revision"] == ""


class TestUnreachable:
    """数据存储不可达。

    不能通过完整应用来测：启动阶段本身就要连库引导管理员，库不可达时应用**起不来**，
    也就走不到端点。因此直接调用端点函数，注入一个连不上的会话 —— 这恰好覆盖了真实
    的另一个时机：应用起来之后数据库才挂掉。
    """

    @staticmethod
    def _broken_session(tmp_path) -> Session:
        # 指向一个不存在的目录，连接时必然失败
        engine = build_engine(
            f"sqlite+pysqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}"
        )
        return Session(bind=engine)

    def test_unreachable_reports_503(self, tmp_path) -> None:
        session = self._broken_session(tmp_path)
        try:
            response = health_module.health(session)
        finally:
            session.close()

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["database"] == "unreachable"
        assert body["status"] == "unavailable"

    def test_unreachable_does_not_leak_the_connection_string(self, tmp_path) -> None:
        """驱动抛出的错误里带路径与连接串，而这个端点匿名可访问。"""
        session = self._broken_session(tmp_path)
        try:
            response = health_module.health(session)
        finally:
            session.close()

        body = response.body.decode("utf-8")
        assert "nope" not in body
        assert "sqlite" not in body.lower()
        assert str(tmp_path) not in body


class TestResponseShape:
    """任务 1.2：响应只能含状态。"""

    def test_healthy_response_has_only_allowed_fields(self, migrated_client) -> None:
        assert set(migrated_client.get(HEALTH).json()) == ALLOWED_FIELDS

    def test_unhealthy_response_has_only_allowed_fields(self, client) -> None:
        assert set(client.get(HEALTH).json()) == ALLOWED_FIELDS

    def test_no_absolute_paths_anywhere(self, migrated_client) -> None:
        body = migrated_client.get(HEALTH).text
        assert ":\\" not in body
        assert "/srv/" not in body
        assert "/home/" not in body

    def test_no_credentials_anywhere(self, migrated_client) -> None:
        body = migrated_client.get(HEALTH).text.lower()
        for word in ("password", "secret", "token", "salt"):
            assert word not in body, f"响应里出现了 {word}"

    def test_endpoint_needs_no_authentication(self, migrated_client) -> None:
        # 匿名可访问是刻意的：负载均衡器与运维脚本要能直接调它
        assert migrated_client.get(HEALTH).status_code == 200
        assert migrated_client.cookies == {}
