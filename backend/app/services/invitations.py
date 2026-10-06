"""邀请码的业务规则。

准入的判定、额度的计算、次数的消耗都在这里；仓储只管存取。**不 import fastapi** ——
与其余服务一致，这一层可以脱离 Web 框架被测试。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.enums import PlatformSwitchKey, UserRole
from app.core.exceptions import (
    BadRequest,
    Forbidden,
    InvitationInvalid,
    InvitationIssuancePaused,
    InvitationNotDeletable,
    InvitationQuota,
)
from app.core.security import generate_invitation_token
from app.db.models import InvitationCode, InvitationRedemption, User
from app.repositories.invitations import (
    InvitationRedemptionRepository,
    InvitationRepository,
    PlatformSwitchRepository,
)
from app.repositories.users import UserRepository

#: 用户自助申请的码的规格。
#:
#: **刻意不做成配置项。** 规格把它们写死了（有效期 7 天、可用 1 次、每 24 小时一张），
#: 而配置会允许运维把它们改成规格之外的值 —— 那要么是规格说谎，要么是配置白设。
#: 哪天需要可调，先改规格。
USER_CODE_TTL_DAYS = 7
USER_CODE_MAX_USES = 1
USER_CODE_INTERVAL_HOURS = 24

#: 管理员建码时的上限，防止一个手误把有效期填成十年或次数填成一百万
MAX_TTL_DAYS = 3650
MAX_USES_LIMIT = 100_000


@dataclass(frozen=True)
class CodeUsage:
    """一次使用：谁、在何时。"""

    #: 使用者账号已被删除时为 None —— 记录还在，只是不知道是谁了
    username: str | None
    used_at: datetime


@dataclass(frozen=True)
class CodeView:
    """一张码，连同它的使用信息。"""

    code: InvitationCode
    usages: list[CodeUsage] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(self.code.max_uses - self.code.used_count, 0)


class InvitationService:
    def __init__(
        self,
        *,
        codes: InvitationRepository | None = None,
        redemptions: InvitationRedemptionRepository | None = None,
        switches: PlatformSwitchRepository | None = None,
        users: UserRepository | None = None,
    ) -> None:
        self.codes = codes or InvitationRepository()
        self.redemptions = redemptions or InvitationRedemptionRepository()
        self.switches = switches or PlatformSwitchRepository()
        self.users = users or UserRepository()

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------

    def invitations_paused(self, session: Session) -> bool:
        return self.switches.is_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED)

    def issuance_paused(self, session: Session) -> bool:
        return self.switches.is_enabled(
            session, PlatformSwitchKey.INVITATION_ISSUANCE_PAUSED
        )

    def switch_states(self, session: Session) -> dict[str, bool]:
        return self.switches.all_states(session)

    def set_switch(
        self,
        session: Session,
        *,
        actor: User,
        key: PlatformSwitchKey,
        enabled: bool,
    ) -> bool:
        """改一个开关。**立即生效** —— 它存在库里，下一次请求就读到新值。"""
        if actor.role != UserRole.ADMIN.value:
            raise Forbidden()
        self.switches.set_enabled(session, key, enabled=enabled, actor_id=actor.id)
        return enabled

    # ------------------------------------------------------------------
    # 注册准入（第一阶段）
    # ------------------------------------------------------------------

    def ensure_registration_allowed(
        self, session: Session, *, token: str | None
    ) -> InvitationCode:
        """校验邀请码并返回它。**任何不合格都抛同一个 `InvitationInvalid`。**

        校验放在这里而不是核销时：不合格的人不该进到"等邮件"那一步，也不该因此产生
        一条占位（design.md 决策 1）。
        """
        if not token or not token.strip():
            raise InvitationInvalid()

        # 暂停优先：它连**此前发出、仍在有效期内**的码一并拒掉（决策 10）
        if self.invitations_paused(session):
            raise InvitationInvalid()

        code = self.codes.get_by_token(session, token.strip())
        if code is None:
            raise InvitationInvalid()

        now = utcnow()
        if (
            code.revoked_at is not None
            or code.expires_at <= now
            or code.used_count >= code.max_uses
        ):
            # 三种情形归到同一句：区分它们等于给匿名接口一个枚举器（决策 5）
            raise InvitationInvalid()

        return code

    # ------------------------------------------------------------------
    # 消耗（第二阶段，与建号同事务）
    # ------------------------------------------------------------------

    def consume(
        self, session: Session, *, code_id: int | None, user_id: int
    ) -> None:
        """扣一次次数并写下使用记录。

        `code_id` 为空表示这条占位建于本功能上线之前 —— 那时准入不校验邀请码，
        因此没有什么可消耗的，放行。

        **扣减失败必须抛错。** 它意味着这张码在"提交注册"与"点邮件"之间被用尽了：
        此时建号会让次数与账号数不符，所以宁可让这次核销失败（整体回滚、占位与链接
        都还在），也不能建出一个没被计入的账号。
        """
        if code_id is None:
            return

        now = utcnow()
        if not self.codes.claim_use(session, code_id=code_id, now=now):
            raise InvitationInvalid()

        self.redemptions.add(
            session, InvitationRedemption(code_id=code_id, user_id=user_id, used_at=now)
        )

    # ------------------------------------------------------------------
    # 用户自助
    # ------------------------------------------------------------------

    def issue_for_user(self, session: Session, *, owner: User, name: str) -> InvitationCode:
        """为用户申请一张码。

        两条额度：**同时只能有一张未使用的**、**每 24 小时一张**。两条都依据持久
        状态判定 —— 用进程内计数器的话，一次重启就能绕过（决策 3）。
        """
        if self.issuance_paused(session):
            raise InvitationIssuancePaused()

        cleaned = (name or "").strip()
        if not cleaned:
            raise BadRequest("请给这张邀请码起个名字")
        if len(cleaned) > 64:
            raise BadRequest("名称最多 64 个字符")

        now = utcnow()
        if self.codes.count_unused_for_owner(session, owner.id, now=now) > 0:
            raise InvitationQuota("你已经有一张尚未使用的邀请码")

        latest = self.codes.latest_created_at(session, owner.id)
        if latest is not None and now - latest < timedelta(hours=USER_CODE_INTERVAL_HOURS):
            raise InvitationQuota("每 24 小时只能申请一张邀请码")

        code = InvitationCode(
            token=generate_invitation_token(),
            name=cleaned,
            owner_id=owner.id,
            max_uses=USER_CODE_MAX_USES,
            used_count=0,
            expires_at=now + timedelta(days=USER_CODE_TTL_DAYS),
        )
        return self.codes.add(session, code)

    def delete_own(self, session: Session, *, owner: User, code_id: int) -> None:
        """删除自己**尚未使用**的码。

        已经用过的不能删：它是使用记录的归属（决策 9）。删除**不重置** 24 小时额度 ——
        额度看的是"最近一次申请的时间"，删掉码不会让那件事没发生过。因此这是**软删**：
        行留下当证据，只是不再出现在用户的列表里。
        """
        code = self._own_code(session, owner=owner, code_id=code_id)
        if code.used_count > 0:
            raise InvitationNotDeletable()
        self.codes.soft_delete(session, code, now=utcnow())

    def list_own(self, session: Session, *, owner: User) -> list[CodeView]:
        return self._views(session, self.codes.list_for_owner(session, owner.id))

    # ------------------------------------------------------------------
    # 管理员
    # ------------------------------------------------------------------

    def list_all(self, session: Session) -> list[CodeView]:
        return self._views(session, self.codes.list_all(session))

    def create(
        self,
        session: Session,
        *,
        actor: User,
        token: str | None,
        name: str,
        days: int,
        max_uses: int,
    ) -> InvitationCode:
        """管理员建码。可指定 token、名称、有效期与可用次数。

        **不受普通用户那两条额度限制**：那是为个人分享设计的，而管理员建码是运营动作
        （决策 8）。码的 `owner_id` 为空 —— 平台码，不归任何个人。
        """
        if actor.role != UserRole.ADMIN.value:
            raise Forbidden()

        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise BadRequest("请给这张邀请码起个名字")
        if len(cleaned_name) > 64:
            raise BadRequest("名称最多 64 个字符")

        cleaned_token = (token or "").strip() or generate_invitation_token()
        if len(cleaned_token) > 64:
            raise BadRequest("token 最多 64 个字符")
        if self.codes.get_by_token(session, cleaned_token) is not None:
            raise BadRequest("这个 token 已经被使用了")

        if days < 1 or days > MAX_TTL_DAYS:
            raise BadRequest(f"有效期须在 1 到 {MAX_TTL_DAYS} 天之间")
        if max_uses < 1 or max_uses > MAX_USES_LIMIT:
            raise BadRequest(f"可用次数须在 1 到 {MAX_USES_LIMIT} 之间")

        code = InvitationCode(
            token=cleaned_token,
            name=cleaned_name,
            owner_id=None,
            max_uses=max_uses,
            used_count=0,
            expires_at=utcnow() + timedelta(days=days),
        )
        return self.codes.add(session, code)

    def revoke(self, session: Session, *, actor: User, code_id: int) -> InvitationCode:
        """让一张码失效。

        **失效而不是删除**：已经产生使用记录的码必须留住那些记录（决策 9）。
        """
        if actor.role != UserRole.ADMIN.value:
            raise Forbidden()
        code = self.codes.get(session, code_id)
        if code is None:
            raise BadRequest("邀请码不存在")
        self.codes.revoke(session, code, now=utcnow())
        return code

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _own_code(self, session: Session, *, owner: User, code_id: int) -> InvitationCode:
        code = self.codes.get(session, code_id)
        # 不存在与不属于自己给同一个答复：否则这个端点成了"这个 id 存不存在"的探针
        if code is None or code.owner_id != owner.id:
            raise BadRequest("邀请码不存在")
        return code

    def _views(
        self, session: Session, codes: Sequence[InvitationCode]
    ) -> list[CodeView]:
        """把码与它们的使用信息拼起来。

        **一次取回全部记录、一次取回全部用户名**，不逐张查 —— 个人中心要列出"我的
        每张码被谁用了"，逐张查就是两层 N+1。
        """
        if not codes:
            return []

        records = self.redemptions.list_for_codes(session, [c.id for c in codes])
        user_ids = [r.user_id for r in records if r.user_id is not None]
        names = (
            {
                user.id: user.username
                for user in self.users.list_by_ids(session, sorted(set(user_ids)))
            }
            if user_ids
            else {}
        )

        grouped: dict[int, list[CodeUsage]] = {}
        for record in records:
            grouped.setdefault(record.code_id, []).append(
                CodeUsage(
                    # 账号已被删除时留 None：记录还在，只是不知道是谁了
                    username=names.get(record.user_id) if record.user_id else None,
                    used_at=record.used_at,
                )
            )

        return [CodeView(code=code, usages=grouped.get(code.id, [])) for code in codes]
