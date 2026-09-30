"""请求与响应模型（Pydantic DTO）。

只用于边界层：进出的 HTTP 形状定义在这里，领域对象留在 models/services。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.db.models import User


class UserPublic(BaseModel):
    """对外暴露的用户信息。

    刻意**不含** `password_hash`、会话凭据与重置凭据——把它们加进来是最容易
    发生的泄露方式。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    email: str | None = None
    email_verified: bool = False

    @classmethod
    def from_model(cls, user: User) -> "UserPublic":
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            email=user.email,
            email_verified=user.email_verified_at is not None,
        )


__all__ = ["UserPublic"]
