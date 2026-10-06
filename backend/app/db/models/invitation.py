"""邀请码与使用记录。

准入机制的载体：注册必须持一张有效邀请码（见 `openspec/specs/invitation-codes/`）。

**与早先那个配置项不是一回事。** 曾经有过 `REGISTRATION_INVITE_CODE` —— 一个全体
共享的口令、默认关闭的开关。它被移除的理由（"一个人退出就得通知所有人换码"）正是
这里用**归属 + 次数 + 有效期**解决的问题：一条码出问题只处理那一条。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import Base
from app.db.types import UtcDateTime


class InvitationCode(Base):
    """一张邀请码。

    `owner_id` 为空即**平台码**：由管理员在管理台创建，不归属任何个人，也不占任何人
    的申请额度（design.md 决策 8）。有归属的是普通用户在个人中心自助申请的。
    """

    __tablename__ = "invitation_codes"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 用户在注册页手动输入的那一串。用户申请的码由系统生成；管理员的码可自定
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # 给码起的名字，用来区分"给张三"和"给李四" —— 这些码本来就是线下转交的
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    # **SET NULL 而不是 CASCADE。** 账号删除功能已经存在：发码人注销时，他发出去的码
    # 与**已经用它注册进来的人**都不该受影响 —— CASCADE 会把码连同使用记录一起删掉，
    # 那等于让一个人注销顺手抹掉"谁邀请了他"这件事。归属因此变为不可考，码与记录都留下。
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 可用次数与已用次数。**扣减必须是单语句条件更新**（`WHERE used_count < max_uses`），
    # 见 `repositories/invitations.py` —— 先查再写会让一张一次性的码用出两个账号。
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)

    # 管理员让码失效。**软删**：已经产生使用记录的码必须留住记录，所以这里不物理删除
    # （design.md 决策 9）。
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # 用户删掉自己**尚未使用**的码。同样是软删 —— 物理删除会连带抹掉 24 小时间隔的
    # 证据（那条规则问的是"最近一次申请是什么时候"，而那个时间就记在这张码上）。
    # 删掉它，用户只要"申请 → 删除 → 申请"就能无限制造邀请码。对用户不可见，但仍是
    # 额度判定的依据（design.md 决策 9）。
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InvitationCode {self.token!r} used={self.used_count}/{self.max_uses}>"


class InvitationRedemption(Base):
    """一次使用：谁、在何时、用了哪张码。

    **独立成表而不是在码上加两列。** `max_uses` 可以大于 1（管理员建的码），因此
    "被谁用过"是一对多，塞不进两列；而且使用者是可空的外键，用户名还会变 —— 记 id
    并 join 比把用户名冗余进来更耐用（design.md 决策 6）。
    """

    __tablename__ = "invitation_redemptions"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 码被删除时记录随之消失 —— 但码只在**未被使用**时才可能被真删，那时本来就没有
    # 记录。管理员用的是失效（revoked_at），不触发这里。
    code_id: Mapped[int] = mapped_column(
        ForeignKey("invitation_codes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 用它注册进来的人。账号再被删除时置空：记录留下，只是不知道是谁了
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    used_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InvitationRedemption code={self.code_id} user={self.user_id}>"
