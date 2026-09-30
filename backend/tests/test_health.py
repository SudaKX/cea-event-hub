"""任务 1.4：应用装配与健康检查。"""

from __future__ import annotations

from app.api.v1 import api_router
from app.core.config import settings


def test_health_returns_200(client) -> None:
    response = client.get(f"{settings.API_PREFIX}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == settings.APP_NAME


def test_api_router_uses_configured_prefix() -> None:
    # nginx 按此前缀把请求导到后端，前缀必须来自配置而非硬编码
    assert api_router.prefix == settings.API_PREFIX
    assert settings.API_PREFIX == "/api/v1"


def test_health_is_not_served_outside_prefix(client) -> None:
    # 根路径归 SPA，后端不应占用
    assert client.get("/health").status_code == 404


def test_docs_live_under_api_prefix(client) -> None:
    # nginx 只反代 /api/ 与 /content/，文档挂在根路径会打到 SPA 上
    assert client.get("/openapi.json").status_code == 404
    assert client.get(f"{settings.API_PREFIX}/openapi.json").status_code == 200


def test_api_responses_carry_no_cors_headers(client) -> None:
    """决策 5 的封锁链：/api/** 绝不能带 CORS 头。

    一旦这里出现 Access-Control-Allow-Origin，沙箱活动页就能绕过宿主直接
    调用后端，桥接代理不再是物理边界。
    """
    for path in (f"{settings.API_PREFIX}/health", "/api/v1/definitely-not-here"):
        headers = client.get(path).headers
        assert "access-control-allow-origin" not in {k.lower() for k in headers}
        assert "access-control-allow-credentials" not in {k.lower() for k in headers}
