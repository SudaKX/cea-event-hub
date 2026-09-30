"""一次性令牌：签发与兑换。

邮箱验证与口令重置**共用一张表、一个兑换端点**。两条签发路径（用户自助 /
管理员线下转交）只是来源不同，凭据本身完全一样，因此"没有邮件基础设施"只是
换一条签发路径，而不是失去该功能（design.md 决策 12）。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.enums import TokenPurpose
from app.core.exceptions import TokenInvalid
from app.core.security import generate_token, hash_token
from app.db.models import User, UserToken
from app.repositories.users import UserTokenRepository


class UserTokenService:
    def __init__(
        self, settings: Settings, repo: UserTokenRepository | None = None
    ) -> None:
        self.settings = settings
        self.repo = repo or UserTokenRepository()

    def issue(
        self,
        session: Session,
        *,
        user: User,
        purpose: TokenPurpose,
        ttl_seconds: int | None = None,
    ) -> str:
        """签发一个令牌，返回**明文**（只在此刻存在一次）。

        签发前作废该用户同用途的旧令牌：同时存在多个有效凭据会让"我点了两次
        忘记密码"变成两条都能用的链接。
        """
        self.repo.invalidate_outstanding(session, user.id, purpose.value)

        token = generate_token()
        self.repo.add(
            session,
            UserToken(
                user_id=user.id,
                purpose=purpose.value,
                token_hash=hash_token(token),
                expires_at=utcnow()
                + timedelta(seconds=ttl_seconds or self.settings.EMAIL_TOKEN_TTL_SECONDS),
            ),
        )
        return token

    def consume(
        self, session: Session, *, token: str, purpose: TokenPurpose
    ) -> UserToken:
        """校验并**标记已使用**，返回令牌记录。

        一次性：用过即废。三种失败（不存在 / 用途不符 / 已用 / 过期）统一返回
        同一个错误，避免把"这个令牌存在但过期了"泄露出去。
        """
        record = self.repo.get_by_hash(session, hash_token(token))
        if record is None:
            raise TokenInvalid()
        if record.purpose != purpose.value:
            raise TokenInvalid()
        if record.used_at is not None:
            raise TokenInvalid()
        if record.expires_at <= utcnow():
            raise TokenInvalid()

        record.used_at = utcnow()
        return record

    def peek(
        self, session: Session, *, token: str, purpose: TokenPurpose
    ) -> UserToken | None:
        """只读校验，不标记使用。供"这个链接还有效吗"这类展示用。"""
        record = self.repo.get_by_hash(session, hash_token(token))
        if record is None or record.purpose != purpose.value:
            return None
        if record.used_at is not None or record.expires_at <= utcnow():
            return None
        return record


__all__ = ["UserTokenService"]
