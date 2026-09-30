"""管理端接口。

**整层挂 `require_admin`**（router 级依赖），而不是逐个端点自己加守卫——后者
迟早会漏掉一个。需要用到管理员对象的地方再单独声明 `AdminUser` 参数。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import AdminUser, DbSession, RuntimeSettings, require_admin
from app.schemas.auth import ResetTokenResponse
from app.services.auth import AuthService

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/users/{user_id}/reset-token",
    response_model=ResetTokenResponse,
    summary="为用户签发一次性口令重置令牌",
)
def issue_reset_token(
    user_id: int,
    session: DbSession,
    admin: AdminUser,
    settings: RuntimeSettings,
) -> ResetTokenResponse:
    """邮件不可用时的口令找回路径：管理员签发，线下转交。

    明文只在这个响应里出现一次。此后任何查询接口都不会返回它——库里存的是摘要。
    """
    target, token, expires_at = AuthService(settings).admin_issue_reset_token(
        session, actor=admin, target_id=user_id
    )
    return ResetTokenResponse(
        user_id=target.id,
        username=target.username,
        token=token,
        expires_at=expires_at,
    )


__all__ = ["router"]
