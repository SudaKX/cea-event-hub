"""应用入口。

`uvicorn app.main:app` 启动。部署时必须以单进程运行：

    uvicorn app.main:app --workers 1 --proxy-headers \
        --forwarded-allow-ips 127.0.0.1

单进程是应用层限流语义正确的前提（design.md 决策 10），也与 SQLite 的单写者
模型一致——多进程只会增加锁争用而不提升写入吞吐。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.db.session import db
from app.services.bootstrap import ensure_bootstrap_admin

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def run_startup_tasks(app: FastAPI) -> None:
    """启动时的一次性任务。

    用 `app.state.database` 而不是模块级 `db`，这样测试可以注入临时库，
    不会在开发库里留下痕迹。
    """
    try:
        ensure_bootstrap_admin(app.state.database, app.state.settings)
    except OperationalError as exc:
        # 表不存在时的报错很晦涩（"no such table: users"），这里补一句该怎么做
        logger.error(
            "数据库不可用或尚未建表：%s\n请先执行：alembic upgrade head", exc
        )
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    run_startup_tasks(app)
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings.ensure_directories()

    # 文档与 OpenAPI 挂在 API 前缀之下：nginx 只把 /api/ 与 /content/ 反代到
    # 后端，挂在根路径会打到 SPA 的 history fallback 上。
    api_prefix = settings.API_PREFIX
    app = FastAPI(
        title=settings.APP_NAME,
        lifespan=lifespan,
        docs_url=None if settings.is_production else f"{api_prefix}/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else f"{api_prefix}/openapi.json",
    )

    app.state.settings = settings
    app.state.database = db

    # 刻意不注册 CORSMiddleware。
    # /api/** 必须不返回任何 CORS 响应头——这是沙箱隔离机制的一部分
    # （design.md 决策 5）：活动页处于不透明源，若 /api 允许跨源，桥接代理
    # 就不再是物理边界，活动页可以绕过宿主直接调用后端，包括管理类接口。
    # /content/** 的 ACAO 头由内容托管模块单独设置（决策 5 的不对称）。

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
