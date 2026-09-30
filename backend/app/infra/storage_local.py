"""本地磁盘文件存储。

路径一律由本模块生成，**绝不使用调用方给的文件名**——那是路径穿越的直接入口。
原始文件名只作为元信息入库，用于展示与下载时的建议文件名。

落盘用分块流式写入而不是一次读进内存：单个文件上限 20MB，但并发几个上传就会
把内存放大到不可控。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.core.clock import utcnow
from app.core.exceptions import PayloadTooLarge, StorageError
from app.core.ports import StoredFile

#: 读写的分块大小
CHUNK_SIZE = 64 * 1024

#: 允许保留的扩展名形态。不在白名单内的扩展名被丢弃——
#: 保留它只会给"按扩展名解释内容"的系统留下可乘之机，而我们对内容不做解释。
_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,10}$")

#: 字节嗅探用的魔数表。仅用于管理端展示，不参与任何安全判定：
#: 下载一律强制为通用二进制类型。
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"RIFF", "application/octet-stream"),
)


def safe_extension(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower().lstrip(".")
    return f".{suffix}" if _SAFE_EXTENSION.match(suffix) else ""


#: 文本中允许出现的控制字符：制表、换行、回车
_TEXT_SAFE_CONTROL = frozenset({0x09, 0x0A, 0x0D})


def sniff_mime(head: bytes) -> str:
    """按字节判断类型。仅用于管理端展示，不参与任何安全判定。

    判定顺序：先认魔数，再看是否含二进制控制字节，最后才试着按 UTF-8 解码。
    少了中间那步，`\\x00\\x01\\x02` 这种内容会因为"每个字节都小于 0x80"而被
    当成纯文本。
    """
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime

    if not head:
        return "application/octet-stream"

    sample = head[:512]
    if any(
        byte < 0x20 and byte not in _TEXT_SAFE_CONTROL for byte in sample
    ):
        return "application/octet-stream"

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"


class LocalDiskStorage:
    """`data/{event_id}/{kind}/{yyyy}/{mm}/{uuid}{ext}` 布局。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def event_dir(self, event_id: str) -> Path:
        return self.root / event_id

    def resolve(self, event_id: str, stored_rel: str) -> Path:
        """把相对路径解析为绝对路径，并确认它仍在活动目录内。"""
        base = self.event_dir(event_id).resolve()
        candidate = (base / stored_rel).resolve()
        if candidate != base and base not in candidate.parents:
            raise StorageError("文件路径越界")
        return candidate

    def save(
        self,
        event_id: str,
        *,
        kind: str,
        original_name: str,
        stream: BinaryIO,
        max_bytes: int | None = None,
    ) -> StoredFile:
        moment = utcnow()
        relative = (
            f"{kind}/{moment:%Y}/{moment:%m}/{uuid4().hex}{safe_extension(original_name)}"
        )
        destination = self.resolve(event_id, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        size = 0
        head = b""

        try:
            with destination.open("wb") as target:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise PayloadTooLarge(
                            f"单个文件超过上限（{max_bytes} 字节）"
                        )
                    if len(head) < 512:
                        head += chunk[: 512 - len(head)]
                    digest.update(chunk)
                    target.write(chunk)
        except PayloadTooLarge:
            # 超限时不能留下半个文件
            destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise StorageError("写入文件失败") from exc

        return StoredFile(
            stored_rel=relative,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )

    def open(self, event_id: str, stored_rel: str) -> BinaryIO:
        return self.resolve(event_id, stored_rel).open("rb")

    def exists(self, event_id: str, stored_rel: str) -> bool:
        try:
            return self.resolve(event_id, stored_rel).is_file()
        except StorageError:
            return False

    def size(self, event_id: str, stored_rel: str) -> int:
        return self.resolve(event_id, stored_rel).stat().st_size

    def delete(self, event_id: str, stored_rel: str) -> None:
        try:
            self.resolve(event_id, stored_rel).unlink(missing_ok=True)
        except StorageError:
            # 越界路径不该被删除，也不该让调用方崩掉
            return

    def remove_event(self, event_id: str) -> None:
        import shutil

        shutil.rmtree(self.event_dir(event_id), ignore_errors=True)

    def total_bytes(self, event_id: str) -> int:
        base = self.event_dir(event_id)
        if not base.is_dir():
            return 0
        return sum(path.stat().st_size for path in base.rglob("*") if path.is_file())


__all__ = [
    "CHUNK_SIZE",
    "LocalDiskStorage",
    "safe_extension",
    "sniff_mime",
]
