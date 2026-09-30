"""任务 1.3：统一错误信封与领域异常到 HTTP 的映射。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import exceptions as ex
from app.core.errors import register_exception_handlers
from app.core.exceptions import RateLimited, ValidationFailed

# 任务 1.3 明确点名的映射，加上其余已定义的领域异常
DOMAIN_CASES = [
    ("LoginRequired", 401, "login_required"),
    ("InvalidCredentials", 401, "invalid_credentials"),
    ("Forbidden", 403, "forbidden"),
    ("AccountDisabled", 403, "account_disabled"),
    ("EventClosed", 403, "event_closed"),
    ("NotFound", 404, "not_found"),
    ("TokenInvalid", 400, "token_invalid"),
    ("BadRequest", 400, "bad_request"),
    ("Conflict", 409, "conflict"),
    ("UsernameTaken", 409, "username_taken"),
    ("EmailTaken", 409, "email_taken"),
    ("QuotaExhausted", 409, "quota_exhausted"),
    ("LastAdminProtected", 409, "last_admin_protected"),
    ("PayloadTooLarge", 413, "payload_too_large"),
    ("UnsupportedMediaType", 415, "unsupported_media_type"),
    ("ValidationFailed", 422, "validation_failed"),
    ("StorageError", 500, "storage_error"),
]


@pytest.fixture
def error_client():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/{cls_name}")
    def _raise(cls_name: str):
        raise getattr(ex, cls_name)()

    @app.get("/raise-with-fields")
    def _raise_fields():
        raise ValidationFailed(fields={"contact": "手机号格式不正确", "grade": "必填"})

    @app.get("/raise-rate-limited")
    def _raise_rate_limited():
        raise RateLimited(retry_after=42)

    @app.get("/plain-error")
    def _plain_error():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize(("cls_name", "status", "code"), DOMAIN_CASES)
def test_domain_error_maps_to_status_and_code(
    error_client, cls_name: str, status: int, code: str
) -> None:
    response = error_client.get(f"/raise/{cls_name}")
    assert response.status_code == status, cls_name
    body = response.json()
    assert body["error"]["code"] == code, cls_name
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_envelope_has_exactly_one_error_key(error_client) -> None:
    body = error_client.get("/raise/NotFound").json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) <= {"code", "message", "fields"}


def test_validation_failure_carries_field_map(error_client) -> None:
    # embed-bridge 规格要求活动页能把错误标注到具体输入框
    body = error_client.get("/raise-with-fields").json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["fields"] == {
        "contact": "手机号格式不正确",
        "grade": "必填",
    }


def test_rate_limited_sets_retry_after(error_client) -> None:
    response = error_client.get("/raise-rate-limited")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.headers["Retry-After"] == "42"


def test_unhandled_exception_maps_to_internal_error(error_client) -> None:
    response = error_client.get("/plain-error")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    # 不把内部细节回给客户端
    assert "boom" not in body["error"]["message"]


class TestFrameworkErrorsUseSameEnvelope:
    def test_routed_404_uses_envelope(self, client) -> None:
        response = client.get("/api/v1/definitely-not-here")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_405_uses_envelope(self, client) -> None:
        response = client.post("/api/v1/health")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"

    def test_framework_messages_are_localized(self, client) -> None:
        # 框架默认 detail 是英文，不应漏给前端
        body = client.get("/api/v1/definitely-not-here").json()
        assert body["error"]["message"] == "资源不存在"


class TestErrorCodeTableIsDistinguishable:
    """四种"不能提交"的原因必须状态码可区分（design.md 决策 9）。"""

    def test_reason_codes_are_distinct(self) -> None:
        pairs = {
            "login_required": ex.LoginRequired.status_code,
            "event_closed": ex.EventClosed.status_code,
            "quota_exhausted": ex.QuotaExhausted.status_code,
            "rate_limited": ex.RateLimited.status_code,
        }
        assert pairs == {
            "login_required": 401,
            "event_closed": 403,
            "quota_exhausted": 409,
            "rate_limited": 429,
        }
        assert len(set(pairs.values())) == 4
