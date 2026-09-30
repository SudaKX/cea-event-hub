"""账号与会话的业务规则。

本模块不 import fastapi：抛领域异常，由 api 层的处理器翻译成 HTTP。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.enums import UserRole
from app.core.exceptions import (
    AccountDisabled,
    BadRequest,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    NotFound,
    UsernameTaken,
    ValidationFailed,
)
from app.core.security import (
    generate_token,
    hash_ip,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
    verify_password_dummy,
)
from app.core.text import (
    email_shape_error,
    normalize_email,
    normalize_username,
    password_shape_error,
    truncate,
    username_shape_error,
)
from app.db.models import User, UserSession
from app.repositories.users import SessionRepository, UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(
        self,
        settings: Settings,
        user_repo: UserRepository | None = None,
        session_repo: SessionRepository | None = None,
    ) -> None:
        self.settings = settings
        self.users = user_repo or UserRepository()
        self.sessions = session_repo or SessionRepository()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(
        self,
        session: Session,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        email: str | None = None,
        invite_code: str | None = None,
    ) -> User:
        normalized = normalize_username(username)

        fields: dict[str, str] = {}
        if (problem := username_shape_error(normalized)) is not None:
            fields["username"] = problem
        if (problem := password_shape_error(password)) is not None:
            fields["password"] = problem

        normalized_email: str | None = None
        if email:
            normalized_email = normalize_email(email)
            if (problem := email_shape_error(normalized_email)) is not None:
                fields["email"] = problem

        if fields:
            raise ValidationFailed(fields=fields)

        self._check_invite_code(invite_code)

        if normalized in self.settings.RESERVED_USERNAMES:
            # 保留名清单挡住两件事：抢占 admin 使平台失去管理入口，
            # 以及出现同名普通账号造成"谁是管理员"的混淆
            raise ValidationFailed(fields={"username": "该用户名为系统保留，请换一个"})

        if self.users.get_by_username(session, normalized) is not None:
            raise UsernameTaken()
        if normalized_email and self.users.get_by_email(session, normalized_email):
            raise EmailTaken()

        user = User(
            username=normalized,
            display_name=truncate(display_name or normalized, 64),
            password_hash=hash_password(password),
            email=normalized_email,
            role=UserRole.USER.value,
            is_active=True,
        )
        try:
            self.users.add(session, user)
        except IntegrityError as exc:
            # 上面的查重与这里的唯一约束之间仍有窗口；约束才是权威
            session.rollback()
            raise UsernameTaken() from exc
        return user

    def _check_invite_code(self, provided: str | None) -> None:
        expected = (self.settings.REGISTRATION_INVITE_CODE or "").strip()
        if not expected:
            return
        import hmac

        if not provided or not hmac.compare_digest(provided.strip(), expected):
            raise ValidationFailed(fields={"invite_code": "邀请码不正确"})

    # ------------------------------------------------------------------
    # 登录 / 登出
    # ------------------------------------------------------------------

    def login(
        self,
        session: Session,
        *,
        username: str,
        password: str,
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[User, str]:
        normalized = normalize_username(username)
        user = self.users.get_by_username(session, normalized)

        if user is None:
            # 跑一次等价哈希，让"账号不存在"与"口令错误"耗时相当；
            # 否则响应时间的差异本身就足以枚举账号
            verify_password_dummy()
            raise InvalidCredentials()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentials()

        # 口令校验**之后**才判断停用：这样只有本来就持有正确口令的人才会
        # 得知账号被停用，既不影响防枚举，又能给出准确的提示
        if not user.is_active:
            raise AccountDisabled()

        if needs_rehash(user.password_hash):
            # 参数升级后透明重哈希，用户无感
            user.password_hash = hash_password(password)

        token = self.issue_session(session, user, ip=ip, user_agent=user_agent)
        return user, token

    def issue_session(
        self, session: Session, user: User, *, ip: str = "", user_agent: str = ""
    ) -> str:
        token = generate_token()
        self.sessions.add(
            session,
            UserSession(
                # 只存摘要：库泄露也拿不到可直接使用的会话
                token_hash=hash_token(token),
                user_id=user.id,
                expires_at=utcnow()
                + timedelta(seconds=self.settings.SESSION_TTL_SECONDS),
                ip_hash=self._hash_ip(ip),
                user_agent=truncate(user_agent, 255) if user_agent else None,
            ),
        )
        return token

    def logout(self, session: Session, token: str) -> None:
        self.sessions.delete(session, hash_token(token))

    def _hash_ip(self, ip: str) -> str | None:
        if not ip:
            return None
        return hash_ip(ip, self.settings.IP_HASH_SALT)

    # ------------------------------------------------------------------
    # 修改口令
    # ------------------------------------------------------------------

    def change_password(
        self,
        session: Session,
        *,
        user: User,
        current_password: str,
        new_password: str,
        current_token: str | None = None,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise ValidationFailed(fields={"current_password": "原口令不正确"})

        if (problem := password_shape_error(new_password)) is not None:
            raise ValidationFailed(fields={"new_password": problem})

        if current_password == new_password:
            raise ValidationFailed(fields={"new_password": "新口令不能与原口令相同"})

        user.password_hash = hash_password(new_password)

        # 其他会话立即失效：口令变更通常意味着"我怀疑有人拿到了我的凭据"
        keep = hash_token(current_token) if current_token else None
        self.sessions.revoke_all_for_user(session, user.id, except_token_hash=keep)

    # ------------------------------------------------------------------
    # 管理员操作（用户管理接口复用）
    # ------------------------------------------------------------------

    def set_role(
        self, session: Session, *, actor: User, target_id: int, role: str
    ) -> User:
        target = self._load_target(session, target_id)
        if role not in {UserRole.USER.value, UserRole.ADMIN.value}:
            raise BadRequest("角色取值不合法")

        if target.role == UserRole.ADMIN.value and role != UserRole.ADMIN.value:
            self._assert_not_last_admin(session, target)

        target.role = role
        # 权限变更立即生效：不吊销会话的话，降级后的用户在旧会话里仍有管理权限
        self.sessions.revoke_all_for_user(session, target.id)
        return target

    def set_active(
        self, session: Session, *, actor: User, target_id: int, is_active: bool
    ) -> User:
        target = self._load_target(session, target_id)

        if not is_active:
            if target.id == actor.id:
                raise BadRequest("不能停用自己的账号")
            self._assert_not_last_admin(session, target)

        target.is_active = is_active
        if not is_active:
            self.sessions.revoke_all_for_user(session, target.id)
        return target

    def _load_target(self, session: Session, target_id: int) -> User:
        target = self.users.get(session, target_id)
        if target is None:
            raise NotFound("用户不存在")
        return target

    def _assert_not_last_admin(self, session: Session, target: User) -> None:
        """不能移除最后一个管理员，否则系统会失去管理能力。"""
        from app.core.exceptions import LastAdminProtected

        if self.users.count_admins(session, exclude_id=target.id) == 0:
            raise LastAdminProtected()

    def require_self_or_admin(self, actor: User, target_id: int) -> None:
        if actor.role != UserRole.ADMIN.value and actor.id != target_id:
            raise Forbidden()
