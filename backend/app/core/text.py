"""文本归一化。

归一化必须在**写入前**完成（可移植性规则第 3 条）：SQLite 的 `=` 区分大小写，
而 MySQL 默认排序规则不区分。不在写入前统一，唯一约束在两个库上的行为就会
分叉——同一个用户名在 SQLite 上能重复注册，在 MySQL 上却会撞唯一键。
"""

from __future__ import annotations

import re
import unicodedata

# 分类标签与用户名允许的字符。用于唯一性与路径安全，不是语义校验。
_USERNAME_ALLOWED = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
_USERNAME_MIN = 3
_USERNAME_MAX = 32

_EMAIL_MAX = 255

# 口令长度上下限。上限存在的理由不是安全而是资源：Argon2 的代价随输入增长，
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


def event_id_shape_error(value: str) -> str | None:
    """校验活动标识。

    不允许大写与点号：标识会直接成为目录名，放宽字符集等于给路径处理留下
    需要反复论证的边界情况（大小写不敏感文件系统、`.`/`..`、尾随点等）。
    """
    if not value:
        return "活动标识不能为空"
    if len(value) < _EVENT_ID_MIN:
        return f"活动标识至少 {_EVENT_ID_MIN} 个字符"
    if len(value) > _EVENT_ID_MAX:
        return f"活动标识最多 {_EVENT_ID_MAX} 个字符"
    if not _EVENT_ID.match(value):
        return "活动标识只能包含小写字母、数字、- 与 _，且必须以字母或数字开头结尾"
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


def password_shape_error(value: str) -> str | None:
    """只检查长度。

    不强制"必须含大写/数字/符号"：那类规则会把人推向 Passw0rd! 这种可预测的
    形态，而长度才是真正有效的强度杠杆。
    """
    if not value:
        return "口令不能为空"
    if len(value) < PASSWORD_MIN:
        return f"口令至少 {PASSWORD_MIN} 个字符"
    if len(value) > PASSWORD_MAX:
        return f"口令最多 {PASSWORD_MAX} 个字符"
    return None


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]
