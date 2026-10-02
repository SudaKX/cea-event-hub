"""用户与会话的持久化。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.db.models import User, UserSession, UserToken


class UserRepository:
    """用户读写。只回答"存了什么"，不做权限或规则判断。"""

    def get(self, session: Session, user_id: int) -> User | None:
        return session.get(User, user_id)

    def list_by_ids(self, session: Session, user_ids: Sequence[int]) -> list[User]:
        """一次取回多个用户。

        批量端点存在的意义就是省掉逐条往返；循环 `get` 的话，"批量"只是把 N 次
        请求换成了 N 次查询。
        """
        if not user_ids:
            return []
        return list(
            session.scalars(select(User).where(User.id.in_(list(user_ids)))).all()
        )

    def get_by_username(self, session: Session, username: str) -> User | None:
        """按**归一化后**的用户名查找；调用方负责先归一化。"""
        return session.scalar(select(User).where(User.username == username))

    def get_by_email(self, session: Session, email: str) -> User | None:
        return session.scalar(select(User).where(User.email == email))

    def add(self, session: Session, user: User) -> User:
        session.add(user)
        session.flush()
        return user

    def count(self, session: Session) -> int:
        return session.scalar(select(func.count()).select_from(User)) or 0

    def count_admins(self, session: Session, *, exclude_id: int | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
        if exclude_id is not None:
            statement = statement.where(User.id != exclude_id)
        return session.scalar(statement) or 0

    def list_users(
        self,
        session: Session,
        *,
        role: str | None = None,
        is_active: bool | None = None,
        username_like: str | None = None,
    ) -> Select:
        statement = select(User).order_by(User.id.desc())
        if role is not None:
            statement = statement.where(User.role == role)
        if is_active is not None:
            statement = statement.where(User.is_active.is_(is_active))
        if username_like:
            statement = statement.where(User.username.like(f"%{username_like}%"))
        return statement


class SessionRepository:
    """服务端会话。主键是令牌摘要，不是令牌本身。"""

    def get(self, session: Session, token_hash: str) -> UserSession | None:
        return session.get(UserSession, token_hash)

    def add(self, session: Session, record: UserSession) -> UserSession:
        session.add(record)
        return record

    def delete(self, session: Session, token_hash: str) -> None:
        record = session.get(UserSession, token_hash)
        if record is not None:
            session.delete(record)

    def revoke_all_for_user(
        self, session: Session, user_id: int, *, except_token_hash: str | None = None
    ) -> int:
        """使某用户的全部会话失效（改密、停用、改角色后调用）。

        这是"会话立即失效"得以成立的地方：不做这一步，账号被停用后其既有会话
        会一直有效到自然过期。
        """
        statement = delete(UserSession).where(UserSession.user_id == user_id)
        if except_token_hash is not None:
            statement = statement.where(UserSession.token_hash != except_token_hash)
        result = session.execute(statement)
        return int(result.rowcount or 0)

    def delete_expired(self, session: Session, *, now: datetime) -> int:
        result = session.execute(
            delete(UserSession).where(UserSession.expires_at <= now)
        )
        return int(result.rowcount or 0)


class UserTokenRepository:
    """一次性令牌（邮箱验证与口令重置共用一张表）。"""

    def get_by_hash(self, session: Session, token_hash: str) -> UserToken | None:
        return session.scalar(
            select(UserToken).where(UserToken.token_hash == token_hash)
        )

    def add(self, session: Session, token: UserToken) -> UserToken:
        session.add(token)
        session.flush()
        return token

    def invalidate_outstanding(
        self, session: Session, user_id: int, purpose: str
    ) -> int:
        """签发新令牌时作废该用户同用途的旧令牌，避免同时存在多个有效凭据。"""
        result = session.execute(
            delete(UserToken).where(
                UserToken.user_id == user_id,
                UserToken.purpose == purpose,
                UserToken.used_at.is_(None),
            )
        )
        return int(result.rowcount or 0)

    def delete_expired(self, session: Session, *, now: datetime) -> int:
        result = session.execute(delete(UserToken).where(UserToken.expires_at <= now))
        return int(result.rowcount or 0)
