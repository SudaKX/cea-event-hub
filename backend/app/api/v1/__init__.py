"""API v1 路由聚合。

前缀来自 `settings.API_PREFIX`（默认 `/api/v1`），nginx 按该前缀把请求导到后端。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, health
from app.core.config import settings

api_router = APIRouter(prefix=settings.API_PREFIX)

api_router.include_router(health.router)
api_router.include_router(auth.router)

__all__ = ["api_router"]
