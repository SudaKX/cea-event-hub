"""FastAPI 依赖：请求级会话、当前用户、权限守卫、活动解析。

本模块是**HTTP 适配边界**，因此允许 import fastapi；`services/` 不允许。
两侧的差别由测试强制（见 tests/test_layering.py）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.clientip import FORWARDED_HEADER, resolve_client_ip
from app.core.clock import utcnow
from app.core.config import Settings, settings
from app.core.enums import UserRole
from app.core.exceptions import Forbidden, LoginRequired, NotFound, RateLimited
from app.core.ports import RateLimiter
from app.core.security import hash_token
from app.db.models import Event, User
from app.db.session import Database
from app.repositories.events import EventRepository
from app.repositories.users import SessionRepository, UserRepository

user_repo = UserRepository()
session_repo = SessionRepository()
event_repo = EventRepository()


def get_db(request: Request) -> Iterator[Session]:
    """请求级会话：**一次请求 = 一个事务**。

    这让"配额 CAS 与插入在同一事务"这条要求（design.md 决策 9）不需要 service
    额外编排——同请求内的所有写入本来就同事务。成功则提交，异常则整体回滚。

    读请求也会走一次 commit，但那是空事务，代价可忽略。
    """
    database: Database = request.app.state.database
    session = database.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_runtime_settings(request: Request) -> Settings:
    """配置从 app.state 取，测试可以整体替换。"""
    return request.app.state.settings


RuntimeSettings = Annotated[Settings, Depends(get_runtime_settings)]


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def enforce_rate_limit(
    limiter: RateLimiter, key: str, *, limit: int, window_seconds: int
) -> None:
    """超过阈值就抛 429。

    429 与 409（名额已满）、403（活动已关闭）、401（需要登录）在状态码上刻意
    分开，客户端才能判断"值得重试"还是"到此为止"（task 11.4）。
    """
    settings_rate_limit_disabled = not settings.RATE_LIMIT_ENABLED
    if settings_rate_limit_disabled:
        return
    decision = limiter.hit(key, limit=limit, window_seconds=window_seconds)
    if not decision.allowed:
        raise RateLimited(retry_after=decision.retry_after)


def request_client_ip(request: Request) -> str:
    """把 Request 拆成框架无关的输入，交给 core.clientip 判定。"""
    runtime: Settings = request.app.state.settings
    return resolve_client_ip(
        peer=request.client.host if request.client else "",
        forwarded_for=request.headers.get(FORWARDED_HEADER),
        trusted_proxies=runtime.TRUSTED_PROXY_IPS,
    )


def request_user_agent(request: Request) -> str:
    return request.headers.get("User-Agent", "")


def limit_by_client_ip(
    request: Request,
    limiter: RateLimiter,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
) -> None:
    enforce_rate_limit(
        limiter,
        f"{scope}:ip:{request_client_ip(request)}",
        limit=limit,
        window_seconds=window_seconds,
    )


def limit_by_user(
    limiter: RateLimiter, *, scope: str, user_id: int, limit: int, window_seconds: int
) -> None:
    enforce_rate_limit(
        limiter,
        f"{scope}:u:{user_id}",
        limit=limit,
        window_seconds=window_seconds,
    )


def extract_session_token(request: Request) -> str | None:
    """从 Cookie 或 Authorization 头取出会话凭据。

    两条路径等价（design.md 决策 4）：浏览器走 HttpOnly Cookie——JS 读不到，
    因此 XSS 偷不走；脚本、测试与非浏览器客户端走 Bearer。桥接层不需要读令牌，
    这正是当初能从 Bearer 改为 Cookie 的原因。
    """
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie:
        return cookie

    header = request.headers.get("Authorization", "")
    if header:
        scheme, _, param = header.partition(" ")
        if scheme.lower() == "bearer" and param.strip():
            return param.strip()
    return None


def resolve_user_by_token(session: Session, token: str) -> User | None:
    """校验会话并返回其用户；任一条件不满足即视为未登录。

    每次请求都校验有效期、撤销状态，以及**账号是否仍然启用**。少了最后一项，
    账号被停用后其会话会一直有效到自然过期——那是"停用立即生效"失效的常见原因。
    """
    record = session_repo.get(session, hash_token(token))
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at <= utcnow():
        return None
    user = user_repo.get(session, record.user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(request: Request, session: DbSession) -> User | None:
    """匿名时返回 None，不抛异常——供"登录可选"的接口使用。"""
    token = extract_session_token(request)
    if not token:
        return None
    return resolve_user_by_token(session, token)


CurrentUser = Annotated[User | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> User:
    if user is None:
        raise LoginRequired()
    return user


RequiredUser = Annotated[User, Depends(require_user)]


def require_admin(user: RequiredUser) -> User:
    if user.role != UserRole.ADMIN.value:
        raise Forbidden()
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def get_event(event_id: str, session: DbSession) -> Event:
    event = event_repo.get(session, event_id)
    if event is None:
        raise NotFound("活动不存在")
    return event


CurrentEvent = Annotated[Event, Depends(get_event)]


__all__ = [
    "AdminUser",
    "CurrentEvent",
    "CurrentUser",
    "DbSession",
    "RateLimiterDep",
    "RequiredUser",
    "RuntimeSettings",
    "enforce_rate_limit",
    "extract_session_token",
    "get_current_user",
    "get_db",
    "get_event",
    "get_rate_limiter",
    "get_runtime_settings",
    "limit_by_client_ip",
    "limit_by_user",
    "request_client_ip",
    "request_user_agent",
    "require_admin",
    "require_user",
    "resolve_user_by_token",
]
