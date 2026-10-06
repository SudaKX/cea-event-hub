"""邀请码相关的请求与响应模型。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import InvitationCode


class InvitationUsagePublic(BaseModel):
    """一次使用：谁、在何时。

    `username` 为空表示使用者账号已被删除 —— 记录还在，只是不知道是谁了。
    """

    username: str | None
    used_at: datetime


class InvitationCodePublic(BaseModel):
    """一张邀请码。

    管理端看到的是全部字段；用户端看到的是**自己名下的**那些，字段相同 ——
    两张界面要显示的东西本来就一样（token、名称、有效期、次数、使用情况）。
    """

    id: int
    token: str
    name: str
    #: 可用次数与已用次数。用户自发的码恒为 1 / 0 或 1 / 1
    max_uses: int
    used_count: int
    expires_at: datetime
    #: 管理员让它失效的时刻。**不是删除** —— 已产生记录的码靠它停用
    revoked_at: datetime | None
    created_at: datetime
    #: 是否为平台码（管理员创建，不归属个人）
    is_platform: bool
    usages: list[InvitationUsagePublic] = []


class InvitationListResponse(BaseModel):
    invitations: list[InvitationCodePublic]


class InvitationEnvelope(BaseModel):
    invitation: InvitationCodePublic


class IssueInvitationRequest(BaseModel):
    """用户自助申请。**只能填名称** —— token 由系统生成，有效期与次数由规格写死。"""

    name: str = Field(min_length=1, max_length=64)


class CreateInvitationRequest(BaseModel):
    """管理员创建。四项都可指定。"""

    token: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    #: 有效期天数。上限与服务的校验一致，避免超长值到服务层才失败
    days: int = Field(ge=1, le=3650)
    max_uses: int = Field(ge=1, le=100_000)


class PlatformSwitchesPublic(BaseModel):
    """两个全局开关的当前状态。

    `invitations_paused` 拒绝**一切**邀请码校验（含已发出的）；
    `invitation_issuance_paused` 只挡新申请。
    """

    invitations_paused: bool
    invitation_issuance_paused: bool


class PlatformSwitchRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    enabled: bool


def invitation_to_public(
    code: InvitationCode,
    *,
    usages: Sequence[InvitationUsagePublic] = (),
) -> InvitationCodePublic:
    """把码与它的使用信息拼成对外形状。

    **两个路由共用这一份**（用户端与管理端）：它们显示的字段本来就一样，区别只在
    范围。各写一遍的话，加一个字段时必然漏掉一处 —— 这个形状在本仓库出现过不止一次。
    """
    return InvitationCodePublic(
        id=code.id,
        token=code.token,
        name=code.name,
        max_uses=code.max_uses,
        used_count=code.used_count,
        expires_at=code.expires_at,
        revoked_at=code.revoked_at,
        created_at=code.created_at,
        # 空 owner 即平台码：管理员建的码不归任何个人（design.md 决策 8）
        is_platform=code.owner_id is None,
        usages=list(usages),
    )
