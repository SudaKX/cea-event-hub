"""领域取值枚举。

刻意使用 Python 侧枚举 + 数据库存**基本类型**（字符串或整数），而不是数据库
ENUM 类型：SQLite 与 MySQL 的枚举实现不同（前者是 VARCHAR + CHECK，后者是真
ENUM），用原生枚举会把方言差异带进 schema，破坏迁移纪律。

提交状态用整数而不是字符串，因为它的取值是**有序的**（见 `SubmissionStatus`），
而且后续导出要按这个码值筛选。整数在任何库上都是同一回事。
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class EventStatus(StrEnum):
    DRAFT = "draft"
    LIVE = "live"
    ARCHIVED = "archived"


class SubmissionStatus(IntEnum):
    """提交的审核状态。

    码值是**对外契约**的一部分（导出、筛选都按它走），改动必须配迁移：

    - `IGNORED = 0` —— 不采用。取 0 是因为它在布尔与数值判断里天然表示"否"
    - `RECEIVED = 1` —— 刚收到，尚未处理。新提交的默认值
    - `ACCEPTED = 2` —— 采用

    刻意**没有**"审核中"这一档：三档已经够用，而中间态会让"导出时按状态筛选"
    多出一个语义模糊的桶 —— 管理员要么还没看，要么已经决定。
    """

    IGNORED = 0
    RECEIVED = 1
    ACCEPTED = 2


#: 状态码到人读名称的映射。导出与前端标签共用同一份，避免各自推导。
SUBMISSION_STATUS_LABELS: dict[int, str] = {
    SubmissionStatus.IGNORED: "不采用",
    SubmissionStatus.RECEIVED: "待处理",
    SubmissionStatus.ACCEPTED: "已采用",
}


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
    "SUBMISSION_STATUS_LABELS",
    "StorageState",
    "SubmissionStatus",
    "TokenPurpose",
    "UserRole",
]
