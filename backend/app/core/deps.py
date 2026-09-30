"""FastAPI 依赖：请求级会话、当前用户、权限守卫、活动解析。

本模块是**HTTP 适配边界**，因此允许 import fastapi；`services/` 不允许。
两侧的差别由测试强制（见 tests/test_layering.py）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.enums import UserRole
from app.core.exceptions import Forbidden, LoginRequired, NotFound
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
    "RequiredUser",
    "extract_session_token",
    "get_current_user",
    "get_db",
    "get_event",
    "require_admin",
    "require_user",
    "resolve_user_by_token",
]
