"""认证接口：注册、登录、登出、当前身份、修改口令。

会话凭据只经 HttpOnly Cookie 下发（`Authorization: Bearer` 为等价路径），
**绝不出现在响应体里**——桥接层的活动页也因此拿不到任何可复用凭据。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.core.config import Settings
from app.core.deps import (
    CurrentUser,
    DbSession,
    EmailSenderDep,
    RateLimiterDep,
    RequiredUser,
    RuntimeSettings,
    extract_session_token,
    limit_by_client_ip,
    request_client_ip,
    request_user_agent,
)
from app.schemas import UserPublic
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    RegistrationPendingResponse,
    ResetPasswordRequest,
    TokenRequest,
    UserEnvelope,
    VerifyRegistrationRequest,
)
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _service(settings: Settings) -> AuthService:
    return AuthService(settings)


def _guard_auth_rate(request: Request, limiter, scope: str) -> None:
    """登录/注册/改密共用的来源维度限流。

    没有这层，开放注册 + 无登录限流就等于允许在线口令爆破。
    """
    runtime: Settings = request.app.state.settings
    limit_by_client_ip(
        request,
        limiter,
        scope=f"auth:{scope}",
        limit=runtime.RATE_LIMIT_AUTH_IP_MAX,
        window_seconds=runtime.RATE_LIMIT_AUTH_IP_WINDOW,
    )


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path=settings.SESSION_COOKIE_PATH,
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path=settings.SESSION_COOKIE_PATH,
    )


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RegistrationPendingResponse,
    summary="提交注册（两阶段的第一步）",
)
def register(
    payload: RegisterRequest,
    request: Request,
    session: DbSession,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
    email_sender: EmailSenderDep,
) -> RegistrationPendingResponse:
    """建立待验证占位并发信。**账号此时并不存在。**

    202 而不是 201：这一步只受理了请求，资源（账号）要到邮件链接被打开才创建。
    """
    _guard_auth_rate(request, limiter, "register")

    ongoing = _service(settings).request_registration(
        session,
        username=payload.username,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        email_sender=email_sender,
    )
    return RegistrationPendingResponse(ongoing=ongoing)


@router.post(
    "/register/verify",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="凭邮件链接完成注册（两阶段的第二步）",
)
def verify_registration(
    payload: VerifyRegistrationRequest,
    response: Response,
    session: DbSession,
    settings: RuntimeSettings,
) -> Response:
    """核销占位并建号。

    **不自动登录** —— 与"注册"和"获得会话"是两件明确的事这一贯做法一致。
    """
    _service(settings).verify_registration(session, token=payload.token)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/login", response_model=UserEnvelope, summary="登录")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
) -> UserEnvelope:
    _guard_auth_rate(request, limiter, "login")

    user, token = _service(settings).login(
        session,
        username=payload.username,
        password=payload.password,
        ip=request_client_ip(request),
        user_agent=request_user_agent(request),
    )
    _set_session_cookie(response, settings, token)
    # 响应体里没有令牌，只有用户信息
    return UserEnvelope(user=UserPublic.from_model(user))


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, summary="登出"
)
def logout(
    request: Request,
    response: Response,
    session: DbSession,
    settings: RuntimeSettings,
) -> Response:
    token = extract_session_token(request)
    if token:
        _service(settings).logout(session, token)
    _clear_session_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserEnvelope, summary="当前登录用户")
def me(user: RequiredUser) -> UserEnvelope:
    return UserEnvelope(user=UserPublic.from_model(user))


@router.post(
    "/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改口令"
)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
    user: RequiredUser,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
) -> Response:
    _guard_auth_rate(request, limiter, "password")

    _service(settings).change_password(
        session,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        current_token=extract_session_token(request),
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/forgot-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="发起口令找回（自助路径）",
)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
    email_sender: EmailSenderDep,
) -> Response:
    """无论账号是否存在都返回 204。

    区分"该邮箱已注册"与"未注册"等于免费提供一个账号枚举接口，因此响应恒为
    成功；只有确实存在且启用时才真的发信。
    """
    _guard_auth_rate(request, limiter, "forgot")
    _service(settings).request_password_reset(
        session, identifier=payload.email, email_sender=email_sender
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="凭一次性令牌重置口令",
)
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
) -> Response:
    """两条签发路径（自助邮件 / 管理员签发）共用这一个兑换端点。"""
    _guard_auth_rate(request, limiter, "reset")
    _service(settings).reset_password(
        session, token=payload.token, new_password=payload.new_password
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="凭令牌确认邮箱",
)
def verify_email(
    payload: TokenRequest,
    response: Response,
    session: DbSession,
    settings: RuntimeSettings,
) -> Response:
    _service(settings).verify_email(session, token=payload.token)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/verify-email/request",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="重新发送邮箱验证邮件",
)
def request_email_verification(
    request: Request,
    response: Response,
    session: DbSession,
    user: RequiredUser,
    limiter: RateLimiterDep,
    settings: RuntimeSettings,
    email_sender: EmailSenderDep,
) -> Response:
    _guard_auth_rate(request, limiter, "verify")
    _service(settings).request_email_verification(
        session, user=user, email_sender=email_sender
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


__all__ = ["router"]
