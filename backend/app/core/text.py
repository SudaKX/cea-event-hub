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


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]
