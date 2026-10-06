"""文本归一化。

归一化必须在**写入前**完成（可移植性规则第 3 条）：SQLite 的 `=` 区分大小写，
而 MySQL 默认排序规则不区分。不在写入前统一，唯一约束在两个库上的行为就会
分叉——同一个用户名在 SQLite 上能重复注册，在 MySQL 上却会撞唯一键。
"""

from __future__ import annotations

import re
import unicodedata

from app.core.enums import DEFAULT_SUBMISSION_KIND

# 分类标签与用户名允许的字符。用于唯一性与路径安全，不是语义校验。
_USERNAME_ALLOWED = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
_USERNAME_MIN = 3
_USERNAME_MAX = 32

_EMAIL_MAX = 255

# 密码长度上下限。上限存在的理由不是安全而是资源：Argon2 的代价随输入增长，
# 不封顶就等于给了一条廉价的 CPU 消耗路径。
PASSWORD_MIN = 8
PASSWORD_MAX = 128


def normalize_username(value: str) -> str:
    """NFC + 去首尾空白 + casefold。

    用 casefold 而不是 lower：它对 ß、İ 之类字符也会折叠，避免出现两个
    "看起来不同、排序规则下相同" 的用户名。
    """
    return unicodedata.normalize("NFC", value).strip().casefold()


def normalize_email(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def username_shape_error(value: str) -> str | None:
    """返回不合规的原因；合规时返回 None。"""
    if not value:
        return "用户名不能为空"
    if len(value) < _USERNAME_MIN:
        return f"用户名至少 {_USERNAME_MIN} 个字符"
    if len(value) > _USERNAME_MAX:
        return f"用户名最多 {_USERNAME_MAX} 个字符"
    if not _USERNAME_ALLOWED.match(value):
        return "用户名只能包含小写字母、数字以及 . _ -，且必须以字母或数字开头结尾"
    return None


def email_shape_error(value: str) -> str | None:
    if not value:
        return "邮箱不能为空"
    if len(value) > _EMAIL_MAX:
        return f"邮箱最多 {_EMAIL_MAX} 个字符"
    # 只做最低限度的形状检查：邮箱的真实性只能靠验证链接确认，
    # 正则写得再复杂也无法证明它存在
    if value.count("@") != 1:
        return "邮箱格式不正确"
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        return "邮箱格式不正确"
    if any(ch.isspace() for ch in value):
        return "邮箱不能包含空白字符"
    return None


#: 活动标识的允许形态。它同时是 SPA 路径、内容目录名与数据目录名，
#: 因此必须同时是 URL 安全与文件系统安全的。
_EVENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")
_EVENT_ID_MIN = 3
_EVENT_ID_MAX = 64


def normalize_event_id(value: str | None) -> str:
    """把活动标识归一化为**规范形态**：去首尾空白 + 转小写。

    **只做这两件事。** 这个限制是刻意的，有两条理由：

    1. **两端必须一致。** 前端也要按同一条规则归一化 —— 它自行拼宿主存储的命名空间，
       那条路径不经过后端。`lower()` / `toLowerCase()` 是两种语言里语义一致的最小操作；
       `casefold()` 没有 JS 对应物，Unicode 折叠（NFKC）更没有，引入它们就等于引入
       "后端认得出、前端认不出"的间歇性故障。

       注意 `casefold()` 与本函数**不等价**，这不是笔误：`"ẞ".casefold()` 得到 `"ss"`
       （会被接受成一个合法标识），而 `"ẞ".lower()` 得到 `"ß"`（会被下面的形态校验拒绝）。
       选后者 —— 让非 ASCII 字符一律落在允许集之外。

    2. **它同时是路径安全的一部分。** 标识直接成为目录名，所以调用方 MUST 用本函数的
       **返回值**去做形态校验与路径拼接。校验一个值、落盘另一个值，会让路径安全退化成
       "依赖某个语言 `lower()` 的具体行为"。小写化不会引入 `.` 或 `/`：全角字母、`İ`
       这类字符小写化之后仍落在允许集之外，会被下面的校验拒掉。
    """
    return (value or "").strip().lower()


def event_id_shape_error(value: str) -> str | None:
    """校验活动标识的**规范形态**。

    不允许大写与点号：标识会直接成为目录名，放宽字符集等于给路径处理留下
    需要反复论证的边界情况（大小写不敏感文件系统、`.`/`..`、尾随点等）。

    **本函数刻意不隐式归一化。** 调用方必须先过 `normalize_event_id`，这样"忘了归一化"
    的表现是一次明确的拒绝，而不是一个悄悄存进去的大写标识。大小写只由归一化那一处
    决定，本函数只看字符集。
    """
    if not value:
        return "活动标识不能为空"
    if len(value) < _EVENT_ID_MIN:
        return f"活动标识至少 {_EVENT_ID_MIN} 个字符"
    if len(value) > _EVENT_ID_MAX:
        return f"活动标识最多 {_EVENT_ID_MAX} 个字符"
    if not _EVENT_ID.match(value):
        # 文案按**使用者视角**写：他们输入里的大写是被允许的（会被转成小写），
        # 真正被拒的是字符集与首尾。校验看到的是归一化后的值，但说"只能包含小写字母"
        # 会让一个只写了大写字母的人以为自己错在这里。
        return "活动标识只能包含字母、数字、- 与 _（大写会被转为小写），且必须以字母或数字开头结尾"
    return None


def entry_path_shape_error(value: str) -> str | None:
    """校验内容入口页的相对路径。

    只允许活动内容目录内的相对路径：绝对路径与任何形式的向上跳转都会被拒，
    否则一个活动就能把 iframe 指向别处。
    """
    if not value:
        return "入口页不能为空"
    if len(value) > 255:
        return "入口页路径过长"
    if value.startswith(("/", "\\")) or ":" in value:
        return "入口页必须是相对路径"
    parts = re.split(r"[\\/]+", value)
    if any(part in ("", ".", "..") for part in parts):
        return "入口页路径不能包含空段、. 或 .."
    return None


#: 分类标签的允许形态。它不是语义校验，而是**路径穿越防护**：
#: 该值会进入 data/{event_id}/{kind}/ 的路径，因此必须严格受限。
_KIND_ALLOWED = re.compile(r"^[a-z0-9_-]{1,64}$")


def sanitize_kind(value: str | None) -> str:
    """把客户端给的分类标签收敛成安全的路径片段。

    不合规就回落到固定默认值，而不是报错：标签只是分组用的便利字段，
    为它拒绝整次提交没有道理。注意这里**不做小写化**——大写属于"不合规"，
    回落到默认值，这样"合法值集合"与正则完全一致，不会出现两种理解。
    """
    if not value:
        return DEFAULT_SUBMISSION_KIND
    candidate = value.strip()
    return candidate if _KIND_ALLOWED.match(candidate) else DEFAULT_SUBMISSION_KIND


def password_shape_error(value: str) -> str | None:
    """只检查长度。

    不强制"必须含大写/数字/符号"：那类规则会把人推向 Passw0rd! 这种可预测的
    形态，而长度才是真正有效的强度杠杆。
    """
    if not value:
        return "密码不能为空"
    if len(value) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 个字符"
    if len(value) > PASSWORD_MAX:
        return f"密码最多 {PASSWORD_MAX} 个字符"
    return None


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]
