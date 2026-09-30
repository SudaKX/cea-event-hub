"""用户、会话与一次性令牌。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.core.enums import UserRole
from app.db.base import Base
from app.db.types import UtcDateTime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 存归一化（小写 + 去空白 + NFC）后的用户名，唯一约束建在归一化值上。
    # SQLite 的 `=` 区分大小写而 MySQL 默认排序规则不区分，只有在写入前统一
    # 归一化，两个库的唯一性行为才一致（可移植性规则第 3 条）。
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # 可空唯一：SQLite 与 MySQL 都把 NULL 视为互不相同，因此多个未绑定邮箱
    # 的账号可以共存
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )

    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserRole.USER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


class UserSession(Base):
    """服务端会话。

    主键是令牌的**摘要**而非令牌本身：库泄露也拿不到可直接使用的会话
    （design.md 决策 11）。令牌明文只在登录响应的 Cookie 里出现一次。
    """

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserSession user_id={self.user_id} expires_at={self.expires_at}>"


class UserToken(Base):
    """一次性令牌：邮箱验证与口令重置**共用一张表**。

    两条签发路径（邮件自助 / 管理员线下转交）写同一张表、走同一个兑换端点，
    因此"没有邮件基础设施"只是换成第二条签发路径，而不是失去该功能
    （design.md 决策 12）。同样只存摘要。
    """

    __tablename__ = "user_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserToken user_id={self.user_id} purpose={self.purpose}>"
