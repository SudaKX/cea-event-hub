"""用户与会话的持久化。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import PendingRegistration, User, UserSession, UserToken


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


class PendingRegistrationRepository:
    """待验证的注册占位。

    这层只回答"存了什么"，不做任何规则判断；唯一性的**冲突判定**发生在服务层
    （先查 `users`，再由唯一索引兜住占位之间的冲突），因此这里没有"能不能注册"。
    """

    def find_pair(
        self, session: Session, *, username: str, email: str
    ) -> PendingRegistration | None:
        """取**完全相同**的那一对占位，用于识别重入。"""
        return session.scalar(
            select(PendingRegistration).where(
                PendingRegistration.username == username,
                PendingRegistration.email == email,
            )
        )

    def get_by_username(
        self, session: Session, username: str
    ) -> PendingRegistration | None:
        """查是哪一边冲突。

        唯一的那个索引只会抛一个不带字段信息的完整性错误，而提示必须落在**具体
        输入框**上（"用户名被占"与"邮箱被占"用户要做的事不同），所以这里显式查
        一次；唯一索引仍然在并发窗口里兜底。
        """
        return session.scalar(
            select(PendingRegistration).where(
                PendingRegistration.username == username
            )
        )

    def get_by_email(self, session: Session, email: str) -> PendingRegistration | None:
        return session.scalar(
            select(PendingRegistration).where(PendingRegistration.email == email)
        )

    def delete_expired_conflicting(
        self, session: Session, *, username: str, email: str, now: datetime
    ) -> int:
        """删掉与新请求冲突的**已过期**占位。

        这是"预留到期即刻释放"得以成立的地方。唯一索引不认时间，过期占位在被真正
        删除前会一直占着用户名与邮箱 —— 只靠后台任务的话，用户得等它跑完才能重试。

        条件整体加括号是必需的：`A OR B AND C` 会被解析成 `A OR (B AND C)`，
        于是"用户名冲突且未过期"的行也会被删掉。
        """
        result = session.execute(
            delete(PendingRegistration).where(
                or_(
                    PendingRegistration.username == username,
                    PendingRegistration.email == email,
                ),
                PendingRegistration.expires_at <= now,
            )
        )
        return int(result.rowcount or 0)

    def add(
        self, session: Session, pending: PendingRegistration
    ) -> PendingRegistration:
        session.add(pending)
        session.flush()
        return pending

    def get_by_token_hash(
        self, session: Session, token_hash: str
    ) -> PendingRegistration | None:
        return session.scalar(
            select(PendingRegistration).where(
                PendingRegistration.token_hash == token_hash
            )
        )

    def claim(self, session: Session, *, pending_id: int, token_hash: str, now: datetime):
        """核销：删掉占位并报告是否**由本次调用删掉**。

        以删除本身作为 CAS —— 一条语句内完成"校验凭据 + 占用"，与活动配额、个人
        配额同一形状，任何隔离级别、任何数据库都成立。并发核销同一凭据时至多一个
        `rowcount == 1`，其余拿到 0 而不是撞唯一约束崩 500。

        必须与建号处于**同一事务**：建号失败时整体回滚，占位因此仍在，用户不至于
        既没建成账号又丢了凭据。
        """
        result = session.execute(
            delete(PendingRegistration).where(
                PendingRegistration.id == pending_id,
                PendingRegistration.token_hash == token_hash,
                PendingRegistration.expires_at > now,
            )
        )
        return int(result.rowcount or 0) == 1

    def delete_expired(self, session: Session, *, now: datetime) -> int:
        """兜底清理：删掉全部过期占位。

        没有它的话，无人注册时那些行会无限堆积 —— 而请求时的那种清理只扫与本次
        请求冲突的行。
        """
        result = session.execute(
            delete(PendingRegistration).where(PendingRegistration.expires_at <= now)
        )
        return int(result.rowcount or 0)
