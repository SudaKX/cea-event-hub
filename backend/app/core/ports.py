"""可替换基础设施的端口。

这三个是唯二"现在写简单实现、以后必须换"的地方（限流最终可能换成 Redis、
文件最终可能换成对象存储、邮件从 console 换成 SMTP）。用 Protocol 声明形状，
由 `settings` 决定注入哪个实现——service 只依赖端口，不依赖实现。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Protocol, runtime_checkable


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    #: 建议的重试等待秒数；仅在被拒绝时有意义
    retry_after: int | None = None


@runtime_checkable
class RateLimiter(Protocol):
    """限流端口。

    实现必须是**无共享状态**的：当前用进程内内存实现，配合单进程部署才语义
    正确（多进程会让每个进程各持一份计数，实际阈值放大到 N 倍）。
    """

    def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """记一次命中并返回判定结果。"""
        ...

    def reset(self) -> None:
        """清空全部计数。供测试与运维使用。"""
        ...


@dataclass(frozen=True)
class StoredFile:
    """落盘结果。`stored_rel` 相对于 `data/{event_id}/`。"""

    stored_rel: str
    size_bytes: int
    sha256: str


@runtime_checkable
class FileStorage(Protocol):
    """文件存储端口。

    路径一律由实现自己生成（随机名 + 消毒后的扩展名），**绝不使用调用方给的
    文件名**——那是路径穿越的直接入口，原始文件名只作为元信息入库。
    """

    def save(
        self,
        event_id: str,
        *,
        kind: str,
        original_name: str,
        stream: BinaryIO,
        max_bytes: int | None = None,
    ) -> StoredFile: ...

    def open(self, event_id: str, stored_rel: str) -> BinaryIO: ...

    def exists(self, event_id: str, stored_rel: str) -> bool: ...

    def delete(self, event_id: str, stored_rel: str) -> None: ...

    def size(self, event_id: str, stored_rel: str) -> int: ...


@runtime_checkable
class EmailSender(Protocol):
    """邮件发送端口。

    提供 console 实现，使开发环境无需 SMTP 即可跑通注册、验证与找回全流程。

    `body` 是**纯文本**版本，`html` 是可选的美化版本。**两个都要发**：只发 HTML
    会被反垃圾系统扣分，而纯文本客户端的读者会看到一片空白；两半内容必须一致，
    尤其是链接两边都得有。不支持 HTML 的实现（console）接受但忽略它。

    `idempotency_key` 是给**支持它的后端**用的（目前是 Resend）：同一个键在 24
    小时内重复投递只会真发一封。形状约定为 `<事件类型>/<实体标识>`，例如
    `registration-pending/42`。console 与 SMTP 实现接受但忽略它 —— 端口是公共
    形状，不该为某一个后端收窄。
    """

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
        idempotency_key: str | None = None,
    ) -> None: ...


@runtime_checkable
class EmailSenderFactory(Protocol):
    def __call__(self, settings: object) -> EmailSender: ...


class NullEmailSender:
    """丢弃全部邮件。用于测试。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        #: 与 `sent` 一一对应的幂等键，供测试断言键的位置与形状
        self.keys: list[str | None] = []
        #: 与 `sent` 一一对应的 HTML 版本（None 表示这封只发了纯文本）
        self.htmls: list[str | None] = []

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.sent.append((to, subject, body))
        self.keys.append(idempotency_key)
        self.htmls.append(html)


__all__ = [
    "EmailSender",
    "FileStorage",
    "NullEmailSender",
    "RateLimitDecision",
    "RateLimiter",
    "StoredFile",
]
