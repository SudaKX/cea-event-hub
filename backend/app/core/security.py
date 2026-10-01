"""安全原语：口令哈希、令牌、内容摘要、地址摘要。

**口令与令牌用两种完全不同的哈希**（design.md 决策 11）：

* 口令是低熵的人类输入，必须靠慢哈希与内存硬度抵御离线爆破 -> Argon2id
* 令牌本身已是 256 位随机，爆破不可行；而会话**每个请求都要校验一次**，
  用 Argon2 会给每个请求平白加上几十毫秒 CPU -> 快哈希摘要

一句话记法：口令要"算得慢"，令牌要"猜不到"。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

_hasher = PasswordHasher()

TOKEN_BYTES = 32

# 口令字符集刻意排除易混淆的 l/1/I 与 O/0
_PASSWORD_ALPHABET = (
    "abcdefghijkmnopqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
    "!@#%^&*-_=+"
)
_DEFAULT_PASSWORD_LENGTH = 20

# 用于让"账号不存在"与"口令错误"耗时相当（防账号枚举）。
# 延迟构建：模块导入时不必付这次哈希的代价。
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, HashingError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """参数是否已落后于当前配置；是则在下次登录时透明重哈希。"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, HashingError):
        return True


def verify_password_dummy() -> None:
    """账号不存在时也跑一次等价校验。

    否则"用户不存在"会立刻返回，而"口令错误"要等一次 Argon2 校验，
    响应耗时的差异足以让人枚举出哪些账号存在。
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash("timing-equalization-placeholder")
    try:
        _hasher.verify(_dummy_hash, "definitely-not-the-password")
    except (VerifyMismatchError, VerificationError, InvalidHashError, HashingError):
        pass


def generate_token() -> str:
    """生成高熵令牌（会话凭据、验证与重置凭据共用）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """令牌只存摘要，库泄露也拿不到可直接使用的凭据。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_password(length: int = _DEFAULT_PASSWORD_LENGTH) -> str:
    """生成高强度随机口令，用于首次启动引导。"""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def canonical_json(payload: Any) -> str:
    """规范化序列化：键排序 + 无多余空白。

    用于按体积上限校验 payload（同样内容总得到同样的字节数）。曾经也用于算内容
    指纹做窗口内去重，那个机制已经移除 —— 见 `services/submissions.py` 里的说明。

    不加 `ensure_ascii=False` 的话，中文会被转义成 \\uXXXX，字节数会虚高，
    体积校验就会误伤中文内容。
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def hash_ip(ip: str, salt: str) -> str:
    """客户端地址的加盐摘要。

    必须加盐：IPv4 空间只有 2^32，无盐哈希几秒钟就能反查出全部地址，
    那样这个"隐私保护"字段只是心理安慰。
    """
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
