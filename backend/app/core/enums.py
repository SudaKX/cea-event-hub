"""领域取值枚举。

刻意使用 Python 侧枚举 + 数据库存 `String`，而不是数据库 ENUM 类型：
SQLite 与 MySQL 的枚举实现不同（前者是 VARCHAR + CHECK，后者是真 ENUM），
用原生枚举会把方言差异带进 schema，破坏迁移纪律。
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class EventStatus(StrEnum):
    DRAFT = "draft"
    LIVE = "live"
    ARCHIVED = "archived"


class SubmissionStatus(StrEnum):
    RECEIVED = "received"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TokenPurpose(StrEnum):
    EMAIL_VERIFY = "email_verify"
    PASSWORD_RESET = "password_reset"


class StorageState(StrEnum):
    """文件落盘与落库之间的中间态（design.md 决策 13）。"""

    PENDING = "pending"
    COMMITTED = "committed"


# 提交分类标签的消毒规则：不符合则回落。不是语义校验，是路径穿越防护。
DEFAULT_SUBMISSION_KIND = "_default"


__all__ = [
    "DEFAULT_SUBMISSION_KIND",
    "EventStatus",
    "StorageState",
    "SubmissionStatus",
    "TokenPurpose",
    "UserRole",
]
