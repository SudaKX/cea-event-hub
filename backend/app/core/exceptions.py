"""领域异常与统一错误信封。

**本模块刻意不 import fastapi。** design.md 决策 14 要求 services 层不依赖
Web 框架；把异常定义放在这里（而非 errors.py）使这条纪律成为结构约束而不是
口头约定——service 抛出领域异常，由 api 层的异常处理器翻译成 HTTP 响应。

错误码表是前后端与活动页之间的契约，见 docs/submission-contract.md。
"""

from __future__ import annotations

from typing import Any


def error_body(
    code: str,
    message: str,
    *,
    fields: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造统一错误信封 `{error: {code, message, fields?}}`。

    活动页只需读 `error.code`，不必解析 HTTP 状态码（embed-bridge 规格要求）。
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    if extra:
        error.update(extra)
    return {"error": error}


class DomainError(Exception):
    """所有领域异常的基类。

    子类通过类属性声明 `code` / `status_code` / `message`，从而让
    "抛什么异常" 与 "回什么状态码" 一一对应，无需在 service 里出现 HTTP 概念。
    """

    code: str = "internal_error"
    status_code: int = 500
    message: str = "服务器内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        fields: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.message = message if message is not None else self.message
        self.fields = fields
        self.extra = extra
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        return error_body(self.code, self.message, fields=self.fields, extra=self.extra)


# --------------------------------------------------------------------------
# 4xx：客户端可修正
# --------------------------------------------------------------------------


class BadRequest(DomainError):
    code = "bad_request"
    status_code = 400
    message = "请求不合法"


class ValidationFailed(DomainError):
    code = "validation_failed"
    status_code = 422
    message = "提交内容有误"


class UnsupportedMediaType(DomainError):
    code = "unsupported_media_type"
    status_code = 415
    message = "不支持的请求内容类型"


class PayloadTooLarge(DomainError):
    code = "payload_too_large"
    status_code = 413
    message = "内容超出体积上限"


class LoginRequired(DomainError):
    code = "login_required"
    status_code = 401
    message = "请先登录"


class InvalidCredentials(DomainError):
    code = "invalid_credentials"
    status_code = 401
    message = "用户名或密码不正确"


class TokenInvalid(DomainError):
    code = "token_invalid"
    status_code = 400
    message = "凭据无效或已过期"


class Forbidden(DomainError):
    code = "forbidden"
    status_code = 403
    message = "没有权限执行该操作"


class AccountDisabled(DomainError):
    code = "account_disabled"
    status_code = 403
    message = "账号已被停用"


class EventClosed(DomainError):
    code = "event_closed"
    status_code = 403
    message = "活动当前不接受提交"


class NotFound(DomainError):
    code = "not_found"
    status_code = 404
    message = "资源不存在"


class Conflict(DomainError):
    code = "conflict"
    status_code = 409
    message = "与当前状态冲突"


class UsernameTaken(DomainError):
    code = "username_taken"
    status_code = 409
    message = "该用户名已被占用"


class EmailTaken(DomainError):
    code = "email_taken"
    status_code = 409
    message = "该邮箱已被绑定"


class RegistrationPending(DomainError):
    """该用户名或邮箱已被一条**待验证的注册**占着。

    与 `UsernameTaken` / `EmailTaken` 分成**不同的码**，因为二者指向完全不同的
    下一步：

    - "已被注册"：换个名字，或者去登录 / 找回密码
    - "有待验证的注册"：去查收邮件，或者等它过期

    合并成一句会把第二种情形里的用户送去一个**根本不存在账号**的登录页 —— 他会
    在那里反复试错，而正确动作其实在邮箱里。

    以 `fields` 指出是哪一个字段冲突：前端据此把提示落到具体输入框上。
    """

    code = "registration_pending"
    status_code = 409
    message = "该用户名或邮箱有一条待验证的注册"


class RegistrationConflict(DomainError):
    """核销时发现用户名或邮箱已被真实账号占用。

    这只可能发生在占位存续期间有人注册了同一个名字。占位会被**保留**（核销与建号
    同事务，失败即整体回滚），因此链接在有效期内仍可重试 —— 否则用户会既没建成
    账号又丢了凭据。

    与 `UsernameTaken` 分开，是因为它描述的不是"这次提交有问题"，而是"验证期间
    情况变了"，前端该给的提示也不同。
    """

    code = "registration_conflict"
    status_code = 409
    message = "该用户名或邮箱已被占用，无法完成注册"


class QuotaExhausted(DomainError):
    """活动提交条数达到上限。

    刻意使用 409 而非 429：这是**终态**而不是"稍后重试"，用 429 会诱导客户端
    重试一个永远不会成功的请求（design.md 决策 9）。
    """

    code = "quota_exhausted"
    status_code = 409
    message = "该活动名额已满"


class SubmitterQuotaExhausted(DomainError):
    """这个提交者在当前活动下已达份数上限。

    与 `QuotaExhausted` 分开成两个码，因为**可采取的行动完全不同**：活动满额是
    "整个活动没位置了"，而个人满额是"你不能再交了，但别人还可以"。合成一个会让
    活动页没法给出准确的提示。

    同样是 409：对这个人来说也是终态，重试多少次都一样。
    """

    code = "submitter_quota_exhausted"
    status_code = 409
    message = "你在该活动下已达到提交份数上限"


class LastAdminProtected(DomainError):
    code = "last_admin_protected"
    status_code = 409
    message = "不能移除最后一个管理员"


class RateLimited(DomainError):
    code = "rate_limited"
    status_code = 429
    message = "操作过于频繁，请稍后再试"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int | None = None,
        fields: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, fields=fields, extra=extra)


# --------------------------------------------------------------------------
# 5xx：服务端问题
# --------------------------------------------------------------------------


class StorageError(DomainError):
    code = "storage_error"
    status_code = 500
    message = "文件存储失败"


class InternalError(DomainError):
    code = "internal_error"
    status_code = 500
    message = "服务器内部错误"


__all__ = [
    "AccountDisabled",
    "BadRequest",
    "Conflict",
    "DomainError",
    "EmailTaken",
    "EventClosed",
    "Forbidden",
    "InternalError",
    "InvalidCredentials",
    "LastAdminProtected",
    "RegistrationConflict",
    "RegistrationPending",
    "LoginRequired",
    "NotFound",
    "PayloadTooLarge",
    "QuotaExhausted",
    "SubmitterQuotaExhausted",
    "RateLimited",
    "StorageError",
    "TokenInvalid",
    "UnsupportedMediaType",
    "UsernameTaken",
    "ValidationFailed",
    "error_body",
]
