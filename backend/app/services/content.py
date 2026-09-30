"""活动网页内容的托管与投放。

**投放语义是覆盖**（design.md 决策 16）：一次投放整体替换活动内容目录，
`content_version` 递增。选择覆盖而非合并，是因为投放是一次*部署*而不是补丁——
覆盖语义可预测（"目录内容 == 最后一次投放的包内容"），而合并会让"当前线上
是什么"无法从任何单一来源推断。

**解压必须"先全部校验、再开始写"**（决策 17）。边解压边判断会在发现违规时
留下半成品，而规格要求"检测到违规时活动内容目录不被修改"。
"""

from __future__ import annotations

import logging
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import NotFound, PayloadTooLarge, ValidationFailed
from app.repositories.events import EventRepository

logger = logging.getLogger(__name__)

#: 解压后仍视为"可读"的扩展名不设白名单——活动页是自行编写的 HTML，
#: 内容本身不参与执行（/content 是静态只读，且活动页在沙箱中运行）


@dataclass(frozen=True)
class ContentFile:
    path: str
    size_bytes: int


@dataclass(frozen=True)
class DeployResult:
    file_count: int
    total_bytes: int
    content_version: int


class ContentService:
    def __init__(
        self, settings: Settings, repo: EventRepository | None = None
    ) -> None:
        self.settings = settings
        self.repo = repo or EventRepository()

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def event_dir(self, event_id: str) -> Path:
        return self.settings.CONTENT_DIR / event_id

    def data_dir(self, event_id: str) -> Path:
        return self.settings.DATA_DIR / event_id

    def resolve_content_path(self, event_id: str, relative: str) -> Path | None:
        """把活动目录内的相对路径解析为绝对路径；越界时返回 None。

        StaticFiles 自身也会拦穿越，但那条防线只覆盖 HTTP 路径；内部调用
        （入口页校验、清单）同样需要这个判定。
        """
        base = self.event_dir(event_id).resolve()
        candidate = (base / relative).resolve()
        if candidate != base and base not in candidate.parents:
            return None
        return candidate

    # ------------------------------------------------------------------
    # 投放
    # ------------------------------------------------------------------

    def deploy_archive(
        self, session: Session, *, event_id: str, stream: BinaryIO, size_bytes: int
    ) -> DeployResult:
        event = self.repo.get(session, event_id)
        if event is None:
            raise NotFound("活动不存在")

        if size_bytes > self.settings.MAX_CONTENT_ARCHIVE_BYTES:
            raise PayloadTooLarge(
                f"内容包超过上限（{self.settings.MAX_CONTENT_ARCHIVE_BYTES} 字节）"
            )

        base = self.event_dir(event_id)
        base.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".deploy-{event_id}-", dir=str(base.parent))
        )

        try:
            with zipfile.ZipFile(stream) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                self._validate_entries(infos)
                total_bytes = self._extract_all(archive, infos, staging)
        except ValidationFailed:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except zipfile.BadZipFile as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ValidationFailed(
                fields={"file": "不是有效的 zip 压缩包"}
            ) from exc
        except Exception:
            # 任何意外都不该留下半成品
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # 全部校验通过后才动目标目录
        self._swap_in(staging, base)

        version = self.repo.bump_content_version(session, event_id)
        logger.info(
            "活动 %s 内容已投放：%d 个文件，%d 字节，版本 %d",
            event_id,
            len(infos),
            total_bytes,
            version,
        )
        return DeployResult(
            file_count=len(infos), total_bytes=total_bytes, content_version=version
        )

    def _validate_entries(self, infos: list[zipfile.ZipInfo]) -> None:
        if not infos:
            raise ValidationFailed(fields={"file": "内容包里没有文件"})
        if len(infos) > self.settings.MAX_CONTENT_ENTRIES:
            raise ValidationFailed(
                fields={"file": f"内容包条目数超过上限（{self.settings.MAX_CONTENT_ENTRIES}）"}
            )

        total = 0
        for info in infos:
            self._validate_entry(info)
            total += info.file_size
            if total > self.settings.MAX_CONTENT_EXTRACTED_BYTES:
                raise PayloadTooLarge("内容包解压后体积超过上限")

    def _validate_entry(self, info: zipfile.ZipInfo) -> None:
        name = info.filename

        # 符号链接：必须拒绝。否则上传者能用一个链接把 /data 或宿主任意文件读出去。
        unix_mode = info.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            raise ValidationFailed(
                fields={"file": f"内容包包含符号链接条目：{name}"}
            )

        if name.startswith(("/", "\\")) or ":" in name:
            raise ValidationFailed(fields={"file": f"内容包包含绝对路径条目：{name}"})

        parts = PurePosixPath(name.replace("\\", "/")).parts
        if any(part == ".." for part in parts):
            raise ValidationFailed(
                fields={"file": f"内容包包含向上跳转的条目：{name}"}
            )
        if not parts:
            raise ValidationFailed(fields={"file": "内容包包含空条目名"})

    def _extract_all(
        self,
        archive: zipfile.ZipFile,
        infos: list[zipfile.ZipInfo],
        staging: Path,
    ) -> int:
        total = 0
        for info in infos:
            relative = PurePosixPath(info.filename.replace("\\", "/"))
            destination = staging.joinpath(*relative.parts)

            # 纵深防御：上面的校验已经挡掉越界，这里再确认一次落点
            if staging not in destination.parents:
                raise ValidationFailed(
                    fields={"file": f"条目落点越界：{info.filename}"}
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
            total += info.file_size
        return total

    def _swap_in(self, staging: Path, target: Path) -> None:
        """用 staging 整体替换目标目录。

        两步 rename 之间有一个极短的窗口目标不存在。单机部署下这个窗口只在
        管理员投放的那一刻出现，且期间访问会得到 404 而不是坏内容——比
        "边删边写"导致活动页加载到一半资源要好。
        """
        backup = target.with_name(f"{target.name}.previous")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

        if target.exists():
            target.rename(backup)
        staging.rename(target)

        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

    # ------------------------------------------------------------------
    # 清单与清理
    # ------------------------------------------------------------------

    def list_files(self, event_id: str) -> list[ContentFile]:
        base = self.event_dir(event_id)
        if not base.is_dir():
            return []

        files: list[ContentFile] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                files.append(
                    ContentFile(
                        path=path.relative_to(base).as_posix(),
                        size_bytes=path.stat().st_size,
                    )
                )
        return files

    def has_entry(self, event_id: str, entry_path: str) -> bool:
        resolved = self.resolve_content_path(event_id, entry_path)
        return resolved is not None and resolved.is_file()

    def remove_event_content(self, event_id: str) -> None:
        shutil.rmtree(self.event_dir(event_id), ignore_errors=True)


__all__ = ["ContentFile", "ContentService", "DeployResult"]
