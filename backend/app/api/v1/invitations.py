"""邀请码：用户自助申请的接口。

管理员那一侧（列出全部、创建、失效、两个开关）在 `admin.py` —— 它们挂在
`/admin` 前缀下，与其余管理端点同处，整层由 router 级依赖守着。

这里只处理**自己名下**的码：申请、删除、查看使用信息。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.deps import DbSession, RequiredUser, RuntimeSettings
from app.schemas.invitations import (
    InvitationEnvelope,
    InvitationListResponse,
    InvitationUsagePublic,
    IssueInvitationRequest,
    invitation_to_public,
)
from app.services.invitations import CodeView, InvitationService

router = APIRouter(prefix="/invitations", tags=["invitations"])


def render(view: CodeView):
    """把服务层的视图转成对外形状。

    两个路由共用 `schemas.invitations.invitation_to_public` —— 显示的字段本来就
    一样，区别只在范围（自己的 / 全部的）。
    """
    return invitation_to_public(
        view.code,
        usages=[
            InvitationUsagePublic(username=usage.username, used_at=usage.used_at)
            for usage in view.usages
        ],
    )


@router.get("", response_model=InvitationListResponse, summary="我发出的邀请码")
def list_mine(
    session: DbSession,
    user: RequiredUser,
    settings: RuntimeSettings,
) -> InvitationListResponse:
    """自己名下的码，含每张的使用情况（被哪个 username、在何时）。

    **不含自己删掉的** —— 删除是软删（额度判定的证据要留住），但对本人不可见。
    """
    views = InvitationService().list_own(session, owner=user)
    return InvitationListResponse(invitations=[render(view) for view in views])


@router.post(
    "",
    response_model=InvitationEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="申请一张邀请码",
)
def issue(
    payload: IssueInvitationRequest,
    session: DbSession,
    user: RequiredUser,
    settings: RuntimeSettings,
) -> InvitationEnvelope:
    """申请一张自己的邀请码。

    token 由系统生成（可手输的短码），有效期 7 天、可用 1 次 —— 这两项**不可指定**，
    它们是规格写死的。两条额度（同时一张未使用、每 24 小时一张）由服务层判定。
    """
    code = InvitationService().issue_for_user(session, owner=user, name=payload.name)
    return InvitationEnvelope(invitation=render(CodeView(code=code)))


@router.delete(
    "/{code_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除一张尚未使用的邀请码",
)
def delete_mine(
    code_id: int,
    session: DbSession,
    user: RequiredUser,
    settings: RuntimeSettings,
) -> Response:
    """删除自己**尚未使用**的码。

    已经用过的不能删：它是使用记录的归属。删除**不重置** 24 小时额度 ——
    额度看的是"最近一次申请是什么时候"，删掉码不会让那件事没发生过。
    """
    InvitationService().delete_own(session, owner=user, code_id=code_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
