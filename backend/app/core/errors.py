"""错误信封的 HTTP 层：异常处理器注册。

领域异常定义在 `core/exceptions.py`（该模块不依赖 fastapi）。本模块只负责把它们
翻译成 HTTP 响应，并把框架自身产生的错误（请求校验、路由未命中、未捕获异常）
也压成同一个信封——否则前端要处理两种错误形状。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import DomainError, RateLimited, error_body

logger = logging.getLogger(__name__)

# 框架产生的状态码 -> 统一错误码。让 404/405 这类也带 code，
# 前端与活动页只需判断 code 而不必解析状态码。
_STATUS_TO_CODE: dict[int, str] = {
    400: "bad_request",
    401: "login_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "rate_limited",
}

_STATUS_TO_MESSAGE: dict[int, str] = {
    400: "请求不合法",
    401: "请先登录",
    403: "没有权限执行该操作",
    404: "资源不存在",
    405: "请求方法不被允许",
    409: "与当前状态冲突",
    413: "内容超出体积上限",
    415: "不支持的请求内容类型",
    422: "提交内容有误",
    429: "操作过于频繁，请稍后再试",
}


def _json_response(
    status_code: int,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    """把所有错误出口收敛到统一信封。"""

    @app.exception_handler(DomainError)
    async def _handle_domain(request: Request, exc: DomainError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            # 限流是唯一带"建议等待时间"的错误（request-rate-limiting 规格）
            headers["Retry-After"] = str(exc.retry_after)
        if exc.status_code >= 500:
            logger.exception("领域异常 %s: %s", exc.code, exc.message)
        return _json_response(exc.status_code, exc.to_body(), headers)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 压成 {字段: 消息}，活动页据此把错误标注到具体输入框
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = [
                str(part)
                for part in err.get("loc", ())
                if part not in ("body", "query", "path", "header", "cookie")
            ]
            key = ".".join(loc) or "_"
            fields[key] = str(err.get("msg", "不合法"))
        return _json_response(
            422,
            error_body("validation_failed", "提交内容有误", fields=fields),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "http_error")
        # 已知状态码一律用本地化文案：框架自身的 detail 是英文（"Not Found"），
        # 与领域错误的中文文案混在一起会让前端看到两种风格。业务代码抛的是
        # DomainError 而非 HTTPException，所以这里不会覆盖掉自定义消息。
        message = _STATUS_TO_MESSAGE.get(exc.status_code)
        if message is None:
            detail = exc.detail
            message = detail if isinstance(detail, str) and detail else "请求失败"
        return _json_response(
            exc.status_code,
            error_body(code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 不把内部细节回给客户端；完整堆栈只进日志
        logger.exception("未捕获异常: %s %s", request.method, request.url.path)
        return _json_response(
            500, error_body("internal_error", "服务器内部错误")
        )
