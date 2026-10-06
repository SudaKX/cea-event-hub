"""账号与会话的业务规则。

本模块不 import fastapi：抛领域异常，由 api 层的处理器翻译成 HTTP。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
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
    InvitationInvalid,
    NotFound,
    RegistrationConflict,
    RegistrationPending,
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
from app.db.models import PendingRegistration, User, UserSession
from app.repositories.submissions import SubmitterQuotaRepository
from app.repositories.users import (
    PendingRegistrationRepository,
    SessionRepository,
    UserRepository,
)
from app.services import email_templates
from app.services.invitations import InvitationService
from app.services.tokens import UserTokenService

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(
        self,
        settings: Settings,
        user_repo: UserRepository | None = None,
        session_repo: SessionRepository | None = None,
        token_service: UserTokenService | None = None,
        pending_repo: PendingRegistrationRepository | None = None,
    ) -> None:
        self.settings = settings
        self.users = user_repo or UserRepository()
        self.sessions = session_repo or SessionRepository()
        self.tokens = token_service or UserTokenService(settings)
        self.pending = pending_repo or PendingRegistrationRepository()
        # 删账号时要清掉该用户名下的配额计数行。那几行没有外键承托、不会随账号消失，
        # 而它们与用户生命周期是同一件事的两半 —— 放在这里，接口层就不必去凑。
        self.submitter_quotas = SubmitterQuotaRepository()
        # 注册准入与邀请码的消耗（两阶段分别落在 request_registration 与
        # verify_registration 里）
        self.invitations = InvitationService()

    # ------------------------------------------------------------------
    # 注册（两阶段：占位 -> 核销建号）
    #
    # 不做"先建账号、再验证邮箱"：那样每一个写错邮箱的注册都会留下一个永远无法
    # 验证的账号 —— 它占着用户名、可能被用来登录、还得靠人工清理。两阶段把一个
    # 笔误变成"什么都没发生"（design.md 决策 19）。
    # ------------------------------------------------------------------

    def request_registration(
        self,
        session: Session,
        *,
        username: str,
        email: str,
        password: str,
        display_name: str | None = None,
        invitation_code: str | None = None,
        email_sender: EmailSender,
    ) -> bool:
        """建立待验证占位并发信。返回是否为**重入**（同一对已存在）。

        重入时不新建、不重发：用户可能是刷新了页面或重按了提交，再发一封只会让
        他收到两封一模一样的邮件，而其中的旧链接仍然有效 —— 那才是真正让人困惑
        的地方。

        **邀请码在建立占位之前校验。** 不合格的人不该进到"等邮件"那一步，也不该
        因此产生一条占位（design.md 决策 1）。校验放在重入短路**之前**：规格要求
        "无邀请码或邀请码无效一律不能注册"，重入也不例外。
        """
        normalized = normalize_username(username)
        normalized_email = normalize_email(email)

        fields: dict[str, str] = {}
        if (problem := username_shape_error(normalized)) is not None:
            fields["username"] = problem
        if (problem := password_shape_error(password)) is not None:
            fields["password"] = problem
        if (problem := email_shape_error(normalized_email)) is not None:
            fields["email"] = problem
        if fields:
            raise ValidationFailed(fields=fields)

        if normalized in self.settings.RESERVED_USERNAMES:
            # 保留名清单挡住两件事：抢占 admin 使平台失去管理入口，
            # 以及出现同名普通账号造成"谁是管理员"的混淆
            raise ValidationFailed(fields={"username": "该用户名为系统保留，请换一个"})

        # 准入。任何不合格都抛同一个错误 —— 区分原因等于给匿名接口一个枚举器
        invitation = self.invitations.ensure_registration_allowed(
            session, token=invitation_code
        )

        if (
            self.pending.find_pair(
                session, username=normalized, email=normalized_email
            )
            is not None
        ):
            return True

        # 先看真实账号：它给出的下一步与"有人正在验证"完全不同
        if self.users.get_by_username(session, normalized) is not None:
            raise UsernameTaken()
        if self.users.get_by_email(session, normalized_email) is not None:
            raise EmailTaken()

        # 到期即释放：唯一索引不认时间，过期的占位在被真正删除前会一直占着槽位。
        # 只靠后台任务的话，用户得等它跑完才能重试。
        now = utcnow()
        self.pending.delete_expired_conflicting(
            session, username=normalized, email=normalized_email, now=now
        )

        self._raise_if_pending(session, normalized, normalized_email)

        token = generate_token()
        pending = PendingRegistration(
            username=normalized,
            email=normalized_email,
            password_hash=hash_password(password),
            display_name=truncate(display_name or normalized, 64),
            token_hash=hash_token(token),
            expires_at=now
            + timedelta(seconds=self.settings.PENDING_REGISTRATION_TTL_SECONDS),
            # 记下这次用的是哪张码：到第二步时用户手里只有邮件里的令牌，
            # 没有那串码，所以必须在这里存下来（design.md 决策 1）
            invitation_code_id=invitation.id,
        )
        try:
            self.pending.add(session, pending)
        except IntegrityError as exc:
            # 上面的检查与唯一索引之间仍有窗口；索引才是权威。
            # 能走到这里说明另一边是**并发创建出来的占位**（真实账号已查过）
            session.rollback()
            raise RegistrationPending() from exc

        self._send_registration_email(
            email_sender,
            to=normalized_email,
            display_name=pending.display_name,
            token=token,
        )
        return False

    def _raise_if_pending(
        self, session: Session, username: str, email: str
    ) -> None:
        """按字段报出"有待验证的注册"。

        唯一的那个索引只会抛一个不带字段信息的完整性错误，而提示必须落在具体的
        输入框上，所以这里显式查一次。
        """
        if self.pending.get_by_username(session, username) is not None:
            raise RegistrationPending(fields={"username": "该用户名有一条待验证的注册"})
        if self.pending.get_by_email(session, email) is not None:
            raise RegistrationPending(fields={"email": "该邮箱有一条待验证的注册"})

    def verify_registration(self, session: Session, *, token: str) -> User:
        """核销占位并建号。**不建立会话** —— 与既有"注册与获得会话是两件事"一致。"""
        digest = hash_token(token)
        pending = self.pending.get_by_token_hash(session, digest)

        # 三种失败（不存在 / 已过期 / 已被别人核销）统一返回同一个错误，
        # 不把"这个凭据存在但过期了"泄露出去
        if pending is None or pending.expires_at <= utcnow():
            raise TokenInvalid()

        # 先把值取出来：下面那条 DELETE 之后，这一行在库里就不存在了
        snapshot = (
            pending.id,
            pending.username,
            pending.email,
            pending.password_hash,
            pending.display_name,
            pending.invitation_code_id,
        )

        if not self.pending.claim(
            session, pending_id=snapshot[0], token_hash=digest, now=utcnow()
        ):
            # 并发的另一个请求先核销了
            raise TokenInvalid()

        _, username, email, password_hash, display_name, invitation_code_id = snapshot
        user = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            email=email,
            # 邮箱刚刚被证明是可达的 —— 这正是本流程的产出
            email_verified_at=utcnow(),
            role=UserRole.USER.value,
            is_active=True,
        )
        try:
            self.users.add(session, user)
            # 消耗邀请码，**与建号同一事务**：扣次数失败（那张码在这两步之间被用尽）
            # 或建号失败时整体回滚 —— 次数不扣、占位仍在、链接仍可重试
            self.invitations.consume(
                session, code_id=invitation_code_id, user_id=user.id
            )
        except IntegrityError as exc:
            # 占位存续期间有人注册了同名账号。整体回滚 → **占位仍在**，
            # 用户不至于既没建成账号又丢了凭据
            session.rollback()
            raise RegistrationConflict() from exc
        except InvitationInvalid:
            # 同上：回滚之后占位与链接都还在，用户可以换一张码重试吗？不能 —— 链接
            # 是一次性的核销凭据，但**占位本身**还在，重新提交注册即可。
            session.rollback()
            raise
        return user

    def _send_registration_email(
        self,
        email_sender: EmailSender,
        *,
        to: str,
        display_name: str,
        token: str,
    ) -> None:
        base = self.settings.PUBLIC_BASE_URL.rstrip("/")
        content = email_templates.registration_email(
            display_name=display_name,
            link=f"{base}/verify-registration?token={token}",
            minutes=self.settings.PENDING_REGISTRATION_TTL_SECONDS // 60,
        )
        self._safe_send(
            email_sender,
            to=to,
            subject=content.subject,
            body=content.text,
            html=content.html,
            # 键按**令牌摘要**取，不按占位行的 id —— 这里踩过一次。
            #
            # `pending_registrations.id` 是 rowid 别名，**没有 AUTOINCREMENT**：一行
            # 被删掉（过期清理、或核销）之后，下一行会拿回同一个 id。于是两封内容
            # 不同的邮件撞上同一个键，Resend 判定为"改了内容的重放"直接拒掉：
            #
            #   This idempotency key has been used ... but the request body was modified
            #
            # 表现是注册接口照常返回 202，而这封信**永远发不出去**。
            #
            # 令牌摘要没有这个问题：每次签发都是一条新消息，摘要随之改变；而**同一条**
            # 消息若将来被重发（目前没有重发路径），摘要不变、正该被去重。
            idempotency_key=f"registration-pending/{hash_token(token)}",
        )

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
            # 跑一次等价哈希，让"账号不存在"与"密码错误"耗时相当；
            # 否则响应时间的差异本身就足以枚举账号
            verify_password_dummy()
            raise InvalidCredentials()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentials()

        # 密码校验**之后**才判断停用：这样只有本来就持有正确密码的人才会
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
    # 修改密码
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
            raise ValidationFailed(fields={"current_password": "原密码不正确"})

        if (problem := password_shape_error(new_password)) is not None:
            raise ValidationFailed(fields={"new_password": problem})

        if current_password == new_password:
            raise ValidationFailed(fields={"new_password": "新密码不能与原密码相同"})

        user.password_hash = hash_password(new_password)

        # 其他会话立即失效：密码变更通常意味着"我怀疑有人拿到了我的凭据"
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
        content = email_templates.password_reset_email(
            display_name=user.display_name,
            link=f"{base}/reset?token={token}",
            hours=self.settings.EMAIL_TOKEN_TTL_SECONDS // 3600,
        )
        self._safe_send(
            email_sender,
            to=user.email or "",
            subject=content.subject,
            body=content.text,
            html=content.html,
            # 键按**令牌摘要**取：同一张令牌重发同一封信会被去重，而重新签发
            # 出来的新令牌是新的一封（那正是用户想要的）
            idempotency_key=f"password-reset/{hash_token(token)}",
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
        content = email_templates.email_verification_email(
            display_name=user.display_name,
            link=f"{base}/verify-email?token={token}",
        )
        self._safe_send(
            email_sender,
            to=user.email,
            subject=content.subject,
            body=content.text,
            html=content.html,
            idempotency_key=f"email-verification/{hash_token(token)}",
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
            "管理员 %r 为用户 %r 签发了密码重置令牌", actor.username, target.username
        )
        return target, token, expires_at

    @staticmethod
    def _safe_send(
        email_sender: EmailSender,
        *,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        """发信失败不影响接口结果。

        让 SMTP 抖动变成 500 会把"邮件服务不可用"伪装成"找回功能坏了"，
        而令牌此时已经签发且有效，用户重试即可。

        `html` 是可选的美化版本，与 `body` 一起发（见端口说明）。
        `idempotency_key` 透传给支持它的后端（Resend），形状
        `<事件类型>/<实体标识>`：同一个键在 24 小时内重复投递只真发一封。
        """
        try:
            email_sender.send(
                to=to,
                subject=subject,
                body=body,
                html=html,
                idempotency_key=idempotency_key,
            )
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

    def bulk_update(
        self,
        session: Session,
        *,
        actor: User,
        target_ids: Sequence[int],
        role: str | None = None,
        is_active: bool | None = None,
    ) -> int:
        """批量改角色与／或启用状态，返回实际改动的条数。

        **最后一个管理员的判定必须整批一起算。** 逐个调用 `set_role` / `set_active`
        是错的：两个管理员一起降级时，第一次检查看到"还有另一个管理员在"（通过），
        第二次检查时第一次的改动还没落库、同样通过 —— 于是**一个管理员都不剩**，
        系统永久失去管理能力。这里改成先算出"改完之后还剩几个可用管理员"。

        「可用管理员」= 角色为 admin **且** 已启用，与 `count_admins` 的口径一致：
        停用一个管理员和把他降级一样，都会减少可用管理员的数量。
        """
        if not target_ids:
            return 0

        if role is not None and role not in {UserRole.USER.value, UserRole.ADMIN.value}:
            raise BadRequest("角色取值不合法")

        targets = self.users.list_by_ids(session, target_ids)
        if not targets:
            return 0

        if is_active is False and any(target.id == actor.id for target in targets):
            raise BadRequest("不能停用自己的账号")

        if role is not None or is_active is not None:
            self._assert_admins_remain(session, targets, role=role, is_active=is_active)

        for target in targets:
            if role is not None:
                target.role = role
            if is_active is not None:
                target.is_active = is_active
            # 权限或状态变了就吊销会话：不吊销的话，降级后的用户在旧会话里仍有管理
            # 权限，而停用也只是"下次登录才生效"
            if role is not None or is_active is False:
                self.sessions.revoke_all_for_user(session, target.id)

        session.flush()
        return len(targets)

    def _assert_admins_remain(
        self,
        session: Session,
        targets: Sequence[User],
        *,
        role: str | None,
        is_active: bool | None,
    ) -> None:
        """按这批改动算完之后，至少还要剩一个可用管理员。"""
        from app.core.exceptions import LastAdminProtected

        def still_admin(target: User) -> bool:
            new_role = role if role is not None else target.role
            new_active = is_active if is_active is not None else target.is_active
            return new_role == UserRole.ADMIN.value and bool(new_active)

        losing = sum(
            1
            for target in targets
            if target.role == UserRole.ADMIN.value
            and target.is_active
            and not still_admin(target)
        )
        if self.users.count_admins(session) - losing <= 0:
            raise LastAdminProtected()

    def delete_user(self, session: Session, *, actor: User, target_id: int) -> None:
        """彻底删除一个账号（design.md 决策 4 与 6）。

        **提交一律保留。** `submissions.submitter` 是派生字符串、没有外键，因此删账号
        不会碰它 —— 那是刻意的：社团收集的数据与"这个人还在不在"是两件事，这也与既有
        的"停用不删历史提交"取值一致。代价是署名从此不可考，只剩一个编号；界面因此
        把它显示成`u:{id}` 加「已删除」标记（本变更第 4 组）。

        删除**不会**让此后的注册继承这些数据 —— 前提是 `users.id` 不再复用，那是本变更
        的迁移所保证的（决策 3）。

        守卫与停用同源：不能删掉最后一个**可用**管理员，不能删自己。删除写一条审计
        日志，与"签发重置令牌"的既有做法一致。
        """
        target = self._load_target(session, target_id)

        if target.id == actor.id:
            raise BadRequest("不能删除自己的账号")
        self._assert_not_last_admin(session, target)

        # 先记下要用的信息：删完之后 target 就成了游离对象
        username = target.username
        submitter = f"u:{target.id}"

        # 会话与令牌由 ON DELETE CASCADE 一并消失；三处审计列由 SET NULL 置空。
        # 同一事务：任何一步失败都整体回滚，不会留下"账号没了但计数还在"的中间态
        self.users.delete(session, target)
        self.submitter_quotas.delete_for_submitter(session, submitter)

        logger.info(
            "管理员 %r 删除了用户 %r（id=%s）；其提交保留，署名成为永久标识 %s",
            actor.username,
            username,
            target_id,
            submitter,
        )

    def _assert_not_last_admin(self, session: Session, target: User) -> None:
        """不能移除最后一个管理员，否则系统会失去管理能力。"""
        from app.core.exceptions import LastAdminProtected

        if self.users.count_admins(session, exclude_id=target.id) == 0:
            raise LastAdminProtected()

    def require_self_or_admin(self, actor: User, target_id: int) -> None:
        if actor.role != UserRole.ADMIN.value and actor.id != target_id:
            raise Forbidden()
