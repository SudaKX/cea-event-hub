"""后台清理任务。

**一处需要说明的实现取舍。** 设计里写的是"写字节 -> 落库为 pending -> 置为
committed"，但落库与置位在**同一个请求事务**里，因此：

* 事务提交了 -> 行是 committed，没有 pending 行
* 事务回滚了 -> 行**根本不存在**，但磁盘上的字节已经写下去了

也就是说，崩溃真正留下的残留不是"pending 记录"，而是**无人认领的字节**。因此
本任务两件事都做：

1. 清掉磁盘上没有对应数据库记录的文件（真正的残留）
2. 处理 `pending` 状态的行：父提交还在就补置为 committed，父提交不在就连记录带
   文件一起删（覆盖将来若把落库拆成两阶段的情形）

第 1 条必须带**宽限期**：正在写入、尚未落库的文件也"没有对应记录"，不加时间
判断就会把进行中的上传删掉。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.enums import StorageState
from app.core.ports import FileStorage, RateLimiter
from app.db.models import Submission
from app.db.session import Database
from app.repositories.submissions import SubmissionFileRepository
from app.repositories.users import SessionRepository, UserTokenRepository

logger = logging.getLogger(__name__)


@dataclass
class JanitorReport:
    orphan_files_removed: int = 0
    pending_files_resolved: int = 0
    sessions_removed: int = 0
    tokens_removed: int = 0
    rate_limit_keys_swept: int = 0

    @property
    def total(self) -> int:
        return (
            self.orphan_files_removed
            + self.pending_files_resolved
            + self.sessions_removed
            + self.tokens_removed
        )


class Janitor:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        storage: FileStorage,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.storage = storage
        self.limiter = limiter
        self.files = SubmissionFileRepository()
        self.sessions = SessionRepository()
        self.tokens = UserTokenRepository()

    def run_once(self) -> JanitorReport:
        report = JanitorReport()
        now = utcnow()
        cutoff = now - timedelta(seconds=self.settings.PENDING_FILE_TTL_SECONDS)

        with self.database.session() as session:
            report.pending_files_resolved = self._resolve_pending(session, cutoff)
            report.sessions_removed = self.sessions.delete_expired(session, now=now)
            report.tokens_removed = self.tokens.delete_expired(session, now=now)
            known = self.files.known_paths(session)

        # 磁盘扫描放在事务之外：它只读文件系统，不该占着数据库连接
        report.orphan_files_removed = self._sweep_orphans(
            known, grace_seconds=self.settings.PENDING_FILE_TTL_SECONDS
        )

        if self.limiter is not None:
            sweep = getattr(self.limiter, "sweep", None)
            if callable(sweep):
                report.rate_limit_keys_swept = sweep(
                    window_seconds=self.settings.RATE_LIMIT_AUTH_IP_WINDOW
                )

        if report.total:
            logger.info(
                "清理任务：孤儿文件 %d，待完成记录 %d，过期会话 %d，过期令牌 %d",
                report.orphan_files_removed,
                report.pending_files_resolved,
                report.sessions_removed,
                report.tokens_removed,
            )
        return report

    def _resolve_pending(self, session, cutoff) -> int:
        resolved = 0
        for record in self.files.list_pending_before(
            session, cutoff=cutoff, state=StorageState.PENDING.value
        ):
            parent = session.get(Submission, record.submission_id)
            if parent is None:
                self.storage.delete(record.event_id, record.stored_rel)
                session.delete(record)
            else:
                # 提交是有效的，只是状态位没落上；补上而不是删除
                record.storage_state = StorageState.COMMITTED.value
            resolved += 1
        return resolved

    def _sweep_orphans(
        self, known: set[tuple[str, str]], *, grace_seconds: int
    ) -> int:
        root = Path(self.settings.DATA_DIR)
        if not root.is_dir():
            return 0

        cutoff_ts = utcnow().timestamp() - grace_seconds
        removed = 0

        for event_dir in root.iterdir():
            if not event_dir.is_dir():
                continue
            event_id = event_dir.name
            for path in event_dir.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(event_dir).as_posix()
                if (event_id, relative) in known:
                    continue
                # 宽限期：正在写入、尚未落库的文件同样"没有记录"
                try:
                    if path.stat().st_mtime > cutoff_ts:
                        continue
                except OSError:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("删除孤儿文件失败：%s", path)

        return removed


__all__ = ["Janitor", "JanitorReport"]
