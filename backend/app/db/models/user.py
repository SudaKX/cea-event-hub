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

    # **SQLite 的 `AUTOINCREMENT` 必须写在这里。** `autoincrement=True` 是整型主键
    # 的默认值，但它**不会**产生 `AUTOINCREMENT` 关键字 —— 没有那个关键字时，SQLite
    # 给新行分配的 id 是 `max(id)+1`，于是删掉 id 最大的那一行之后，下一条插入会
    # **拿回同一个 id**。
    #
    # 那对本项目不是小事：`submissions.submitter` 是派生字符串 `u:{user_id}`，没有
    # 外键。id 一旦被复用，新注册的人就会从数据角度"继承"前一个被删者的提交与配额
    # 计数，而且**没有任何报错**。删除账号这个功能因此以它为前提（design.md 决策 3）。
    #
    # MySQL 侧无需对应物：`AUTO_INCREMENT` 计数器自 8.0 起持久化。更老版本重启后按
    # `max(id)+1` 重算，是迁移时的复核项，见 docs/deployment.md。
    __table_args__ = {"sqlite_autoincrement": True}

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


class PendingRegistration(Base):
    """待验证的注册占位。

    注册分两阶段：请求只建这一行，账号在邮箱验证成功后才出现。这样邮箱写错不会
    留下一个永远无法验证的账号 —— 它占着用户名、可能被用来登录、还得靠人工清理；
    两阶段把一个笔误变成"什么都没发生"（design.md 决策 19）。

    **没有外键指向 `users`**，因为这一行存在的整个前提就是那个用户还不存在。

    `username` 与 `email` 各自唯一，因此占位存续期间二者都被保留。注意**唯一索引
    不认时间**：过期的行在被真正删除之前会一直占着这两个槽位，所以清理不是卫生
    工作而是这套机制的一部分（见 16.4 的两处清理）。
    """

    __tablename__ = "pending_registrations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 与 users 同一套归一化：`Alice` 与 `alice` 必须争同一个槽位，否则最后建号时
    # 才会撞上唯一约束
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # 口令在占位期间就以加盐慢哈希存下；明文绝不落库
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)

    # 验证链接的凭据摘要。明文只出现在邮件里
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PendingRegistration username={self.username!r}>"
