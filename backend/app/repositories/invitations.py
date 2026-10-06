"""邀请码与平台开关的持久化。

只回答"存了什么"，不做权限或规则判断 —— 校验顺序、额度判定这些在
`services/invitations.py`。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.enums import PlatformSwitchKey
from app.db.models import InvitationCode, InvitationRedemption, PlatformSwitch


class InvitationRepository:
    """邀请码读写。"""

    def get(self, session: Session, code_id: int) -> InvitationCode | None:
        return session.get(InvitationCode, code_id)

    def get_by_token(self, session: Session, token: str) -> InvitationCode | None:
        return session.scalar(
            select(InvitationCode).where(InvitationCode.token == token)
        )

    def add(self, session: Session, code: InvitationCode) -> InvitationCode:
        session.add(code)
        session.flush()
        return code

    def soft_delete(
        self, session: Session, code: InvitationCode, *, now: datetime
    ) -> None:
        """用户删掉自己尚未使用的码。

        **软删，不物理删除。** 那条规则"每 24 小时只能申请一张"问的是"最近一次申请
        是什么时候"，而那个时间就记在这张码上 —— 删掉行，规则随即失效：用户只要
        "申请 → 删除 → 申请"就能无限制造邀请码。因此行留下（对用户不可见），
        `deleted_at` 只是把可见性关掉（design.md 决策 9）。
        """
        code.deleted_at = now
        session.flush()

    def revoke(self, session: Session, code: InvitationCode, *, now: datetime) -> None:
        code.revoked_at = now
        session.flush()

    def claim_use(self, session: Session, *, code_id: int, now: datetime) -> bool:
        """占用一次可用次数，报告是否**由本次调用占到**。

        **一条语句内完成"校验 + 占用"**：以 `used_count < max_uses` 作为更新条件，
        与 `pending.claim()`（以删除本身作 CAS）、活动配额同一形状，任何隔离级别、
        任何数据库都成立。并发时至多一个 `rowcount == 1`，其余拿到 0。

        **先查再写会怎样：** 两条并发注册会同时读到"还剩一次"，然后各自建号 ——
        一张一次性的码用出两个账号，恰恰是这道门槛要防的事。

        必须与建号处于**同一事务**：建号失败时整体回滚，次数因此不被扣减，占位与
        链接仍可重试（与既有"建号失败占位仍在"同一性质）。
        """
        result = session.execute(
            update(InvitationCode)
            .where(
                InvitationCode.id == code_id,
                InvitationCode.revoked_at.is_(None),
                InvitationCode.expires_at > now,
                InvitationCode.used_count < InvitationCode.max_uses,
            )
            .values(used_count=InvitationCode.used_count + 1)
        )
        return int(result.rowcount or 0) == 1

    def list_for_owner(
        self, session: Session, owner_id: int, *, limit: int | None = None
    ) -> list[InvitationCode]:
        """某人发出的码，新的在前。**不含他自己删掉的** —— 软删的意义就在这里。"""
        statement = (
            select(InvitationCode)
            .where(
                InvitationCode.owner_id == owner_id,
                InvitationCode.deleted_at.is_(None),
            )
            .order_by(InvitationCode.created_at.desc(), InvitationCode.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(session.scalars(statement).all())

    def list_all(self, session: Session) -> list[InvitationCode]:
        """全部码，新的在前。管理台用。"""
        return list(
            session.scalars(
                select(InvitationCode).order_by(
                    InvitationCode.created_at.desc(), InvitationCode.id.desc()
                )
            ).all()
        )

    def latest_created_at(self, session: Session, owner_id: int) -> datetime | None:
        """该用户最近一次申请的时间，没有则 None。

        **24 小时间隔据此判定，而不是限流器。** 限流器是进程内内存态，重启即清零 ——
        那会让一条业务规则被一次重启绕过（design.md 决策 3）。
        """
        return session.scalar(
            select(func.max(InvitationCode.created_at)).where(
                InvitationCode.owner_id == owner_id
            )
        )

    def count_unused_for_owner(
        self, session: Session, owner_id: int, *, now: datetime
    ) -> int:
        """该用户手上**真正还能用**的码有几张。

        四个条件缺一不可：未用尽、未失效、未删除、**未过期**。

        最后一个容易漏 —— 一张过期的码既不能用于注册，却仍占着"同时只能有一张未使用"
        的名额，用户就被一张废码锁住了（design.md 决策 10）。用已用次数而不是"有没有
        使用记录"来判用尽：次数是权威（管理员建的码可以有多次），记录只是它的副产品。
        """
        return (
            session.scalar(
                select(func.count())
                .select_from(InvitationCode)
                .where(
                    InvitationCode.owner_id == owner_id,
                    InvitationCode.revoked_at.is_(None),
                    InvitationCode.deleted_at.is_(None),
                    InvitationCode.expires_at > now,
                    InvitationCode.used_count < InvitationCode.max_uses,
                )
            )
            or 0
        )


class InvitationRedemptionRepository:
    """使用记录读写。"""

    def add(
        self, session: Session, redemption: InvitationRedemption
    ) -> InvitationRedemption:
        session.add(redemption)
        session.flush()
        return redemption

    def list_for_codes(
        self, session: Session, code_ids: Sequence[int]
    ) -> list[InvitationRedemption]:
        """一次取回多张码的记录。

        **批量而不是逐张查。** 个人中心要显示"我的每张码被谁用了"，逐张查就是
        N+1；这个形状在本仓库出现过不止一次（提交者解析、批量审核）。
        """
        if not code_ids:
            return []
        return list(
            session.scalars(
                select(InvitationRedemption)
                .where(InvitationRedemption.code_id.in_(list(code_ids)))
                .order_by(InvitationRedemption.used_at.desc())
            ).all()
        )


class PlatformSwitchRepository:
    """平台级运行时开关的读写。

    **没有行就是关闭** —— 这样开关不需要"初始化"这一步，读取时也不必区分"还没设过"
    与"设成了关闭"。
    """

    def is_enabled(self, session: Session, key: PlatformSwitchKey) -> bool:
        row = session.get(PlatformSwitch, key.value)
        return bool(row.enabled) if row is not None else False

    def set_enabled(
        self,
        session: Session,
        key: PlatformSwitchKey,
        *,
        enabled: bool,
        actor_id: int | None = None,
    ) -> PlatformSwitch:
        row = session.get(PlatformSwitch, key.value)
        if row is None:
            row = PlatformSwitch(
                key=key.value, enabled=enabled, updated_by=actor_id
            )
            session.add(row)
        else:
            row.enabled = enabled
            row.updated_by = actor_id
        session.flush()
        return row

    def all_states(self, session: Session) -> dict[str, bool]:
        """全部已知开关的状态，缺失的补 False。"""
        stored = {
            row.key: bool(row.enabled)
            for row in session.scalars(select(PlatformSwitch)).all()
        }
        return {key.value: stored.get(key.value, False) for key in PlatformSwitchKey}

    def clear(self, session: Session) -> None:
        """删掉全部开关行 —— 测试用来回到"什么都没设过"的状态。"""
        session.execute(delete(PlatformSwitch))
