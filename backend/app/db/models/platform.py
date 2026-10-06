"""平台级的运行时开关。

**这是本项目第一处由数据库支撑的运行时设置。** 在此之前 `RuntimeSettings` 是
`app.state.settings` 的包装，也就是**环境变量的快照**：改它要重启进程。

开关不能那样。` invitations_paused`（暂停邀请）是**应急刹车** —— 发现有人在倒卖
邀请码时要立刻止住，等一次重启就失去了意义。因此两者的分界是：

    配置是部署形态，开关是运营动作。

`invitations_issuance_paused`（暂停申请）管的是"别再制造了"，与前者是两类事故，
所以是两个开关而不是一个 —— 合成一个总有一种场景要迁就另一种（design.md 决策 10）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import Base
from app.db.types import UtcDateTime


class PlatformSwitch(Base):
    """一个具名开关的当前状态。

    没有行就等于**关闭**（默认值）—— 这样开关不需要"初始化"这一步，读取时不必
    区分"还没设过"与"设成了关闭"。
    """

    __tablename__ = "platform_switches"

    # 直接用具名键作主键：开关是有限的、由代码定义的集合，不需要自增 id
    key: Mapped[str] = mapped_column(String(64), primary_key=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    # 谁改的。管理员账号被删除时置空 —— 开关本身的状态不该跟着变
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PlatformSwitch {self.key}={self.enabled}>"
