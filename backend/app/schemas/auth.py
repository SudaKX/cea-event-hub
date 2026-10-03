"""认证相关的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import UserPublic


class RegisterRequest(BaseModel):
    # 长度上限与数据库列宽一致，避免超长值到写库时才失败
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)
    # **必填**：注册要经邮箱验证才算完成，而验证的对象就是它。没有"仅凭用户名
    # 注册"的降级路径
    email: str = Field(min_length=1, max_length=255)


class RegistrationPendingResponse(BaseModel):
    """注册请求已受理。

    `ongoing` 为真表示这是**重入** —— 同一对用户名与邮箱已有一条待验证的占位，
    本次没有新建、也没有重发邮件。前端据此把文案从"邮件已发送"改成
    "我们已经发过一封"，否则用户会去邮箱里找一封并不存在的新邮件。
    """

    ongoing: bool = False


class VerifyRegistrationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserEnvelope(BaseModel):
    """单个用户信息的统一包装。"""

    user: UserPublic


class ForgotPasswordRequest(BaseModel):
    # 用邮箱发起自助找回：只有已绑定并（可选）验证过的邮箱才收得到链接
    email: str = Field(min_length=1, max_length=255)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class ResetTokenResponse(BaseModel):
    """管理员签发重置令牌的响应。

    明文**只在这里出现一次**；库里存的是摘要，因此任何后续查询都拿不到它。
    """

    user_id: int
    username: str
    token: str
    expires_at: datetime


class UserAdminPublic(UserPublic):
    """管理端用户视图。

    比公开视图多出启用状态与创建时间，但**同样不含**口令哈希、重置凭据或
    会话凭据——把它们加进来是最容易发生的泄露方式。
    """

    is_active: bool
    created_at: datetime

    @classmethod
    def from_model(cls, user) -> "UserAdminPublic":  # noqa: ANN001
        return cls(
            **UserPublic.from_model(user).model_dump(),
            is_active=user.is_active,
            created_at=user.created_at,
        )


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    is_active: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=64)


class UserBulkRequest(BaseModel):
    """批量改角色与／或启用状态。

    `role` 与 `is_active` 至少要给一个；两个都给就一起改。**不给 display_name**：
    给一批人设同一个显示名没有意义，逐条改才是对的。
    """

    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(min_length=1, max_length=200)
    role: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_something_to_change(self) -> UserBulkRequest:
        if self.role is None and self.is_active is None:
            raise ValueError("至少要给出 role 或 is_active")
        return self


class UserListResponse(BaseModel):
    users: list[UserAdminPublic]
    total: int


class UserAdminEnvelope(BaseModel):
    user: UserAdminPublic


__all__ = [
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "LoginRequest",
    "RegisterRequest",
    "RegistrationPendingResponse",
    "ResetPasswordRequest",
    "UserBulkRequest",
    "ResetTokenResponse",
    "TokenRequest",
    "UserAdminEnvelope",
    "UserAdminPublic",
    "UserEnvelope",
    "UserListResponse",
    "UserUpdateRequest",
    "VerifyRegistrationRequest",
]
