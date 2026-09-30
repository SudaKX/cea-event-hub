"""认证相关的请求与响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas import UserPublic


class RegisterRequest(BaseModel):
    # 长度上限与数据库列宽一致，避免超长值到写库时才失败
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    # 仅当配置启用了邀请码时才校验
    invite_code: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserEnvelope(BaseModel):
    """单个用户信息的统一包装。"""

    user: UserPublic


__all__ = [
    "ChangePasswordRequest",
    "LoginRequest",
    "RegisterRequest",
    "UserEnvelope",
]
