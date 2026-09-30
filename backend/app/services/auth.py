"""账号与会话的业务规则。

本模块不 import fastapi：抛领域异常，由 api 层的处理器翻译成 HTTP。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.enums import TokenPurpose, UserRole
from app.core.exceptions import (
    AccountDisabled,
    BadRequest,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    NotFound,
    TokenInvalid,
    UsernameTaken,
    ValidationFailed,
)
from app.core.ports import EmailSender
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
from app.services.tokens import UserTokenService

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(
        self,
        settings: Settings,
        user_repo: UserRepository | None = None,
        session_repo: SessionRepository | None = None,
        token_service: UserTokenService | None = None,
    ) -> None:
        self.settings = settings
        self.users = user_repo or UserRepository()
        self.sessions = session_repo or SessionRepository()
        self.tokens = token_service or UserTokenService(settings)

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
    # 凭据找回与邮箱验证
    # ------------------------------------------------------------------

    def request_password_reset(
        self, session: Session, *, identifier: str, email_sender: EmailSender
    ) -> None:
        """发起自助找回。

        无论账号是否存在、是否绑定邮箱，**都返回成功**：区分这两种情形等于免费
        提供一个账号枚举接口。只有确实存在且处于启用状态时才真的发信。
        """
        if not self.settings.ALLOW_SELF_SERVICE_RESET:
            # 未开启自助找回时静默返回：调用方是管理员签发路径
            return

        normalized = normalize_email(identifier)
        user = self.users.get_by_email(session, normalized)
        if user is None or not user.is_active:
            return

        self._send_reset_email(session, user=user, email_sender=email_sender)

    def _send_reset_email(
        self, session: Session, *, user: User, email_sender: EmailSender
    ) -> str:
        token = self.tokens.issue(
            session, user=user, purpose=TokenPurpose.PASSWORD_RESET
        )
        base = self.settings.PUBLIC_BASE_URL.rstrip("/")
        hours = self.settings.EMAIL_TOKEN_TTL_SECONDS // 3600
        self._safe_send(
            email_sender,
            to=user.email or "",
            subject="重置口令",
            body=(
                f"你好 {user.display_name}：\n\n"
                "有人请求重置该账号的口令。如果这是你本人，请打开下面的链接：\n\n"
                f"{base}/reset?token={token}\n\n"
                f"链接 {hours} 小时内有效，且只能使用一次。"
                "如果不是你发起的，忽略本邮件即可。\n"
            ),
        )
        return token

    def reset_password(
        self, session: Session, *, token: str, new_password: str
    ) -> User:
        if (problem := password_shape_error(new_password)) is not None:
            raise ValidationFailed(fields={"new_password": problem})

        record = self.tokens.consume(
            session, token=token, purpose=TokenPurpose.PASSWORD_RESET
        )
        user = self.users.get(session, record.user_id)
        if user is None:
            raise TokenInvalid()

        user.password_hash = hash_password(new_password)
        # 凭据找回的典型场景就是"我怀疑账号被人用过"，因此全部会话失效，
        # 不保留任何既有会话
        self.sessions.revoke_all_for_user(session, user.id)
        return user

    def request_email_verification(
        self, session: Session, *, user: User, email_sender: EmailSender
    ) -> str:
        if not user.email:
            raise ValidationFailed(fields={"email": "尚未绑定邮箱"})

        token = self.tokens.issue(
            session, user=user, purpose=TokenPurpose.EMAIL_VERIFY
        )
        base = self.settings.PUBLIC_BASE_URL.rstrip("/")
        self._safe_send(
            email_sender,
            to=user.email,
            subject="确认邮箱",
            body=(
                f"你好 {user.display_name}：\n\n"
                "请打开下面的链接确认该邮箱地址：\n\n"
                f"{base}/verify-email?token={token}\n\n"
                "如果不是你发起的，忽略本邮件即可。\n"
            ),
        )
        return token

    def verify_email(self, session: Session, *, token: str) -> User:
        record = self.tokens.consume(
            session, token=token, purpose=TokenPurpose.EMAIL_VERIFY
        )
        user = self.users.get(session, record.user_id)
        if user is None:
            raise TokenInvalid()

        user.email_verified_at = utcnow()
        return user

    def admin_issue_reset_token(
        self, session: Session, *, actor: User, target_id: int
    ) -> tuple[User, str, datetime]:
        """管理员签发一次性重置令牌，供线下转交（邮件不可用时的路径）。

        明文只在返回值里出现这一次；库里只有摘要，因此任何后续查询都拿不到它。
        """
        target = self._load_target(session, target_id)
        if not target.is_active:
            raise BadRequest("不能为已停用的账号签发重置令牌")

        token = self.tokens.issue(
            session, user=target, purpose=TokenPurpose.PASSWORD_RESET
        )
        record = self.tokens.peek(
            session, token=token, purpose=TokenPurpose.PASSWORD_RESET
        )
        expires_at = record.expires_at if record else utcnow()

        logger.info(
            "管理员 %r 为用户 %r 签发了口令重置令牌", actor.username, target.username
        )
        return target, token, expires_at

    @staticmethod
    def _safe_send(
        email_sender: EmailSender, *, to: str, subject: str, body: str
    ) -> None:
        """发信失败不影响接口结果。

        让 SMTP 抖动变成 500 会把"邮件服务不可用"伪装成"找回功能坏了"，
        而令牌此时已经签发且有效，用户重试即可。
        """
        try:
            email_sender.send(to=to, subject=subject, body=body)
        except Exception:
            logger.exception("发送邮件失败（收件人 %s，主题 %s）", to, subject)

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
