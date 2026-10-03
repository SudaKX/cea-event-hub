"""提交与其附件。

父行是"提交这个行为"本身，附件挂在它下面。纯文件提交的父行内容为空对象，
因此既不需要可空外键，也不需要第二张表（design.md 决策 15）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.core.enums import StorageState, SubmissionStatus
from app.db.base import Base
from app.db.types import UtcDateTime


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )

    # 统一的提交者标识：u:{user_id} / a:{client_id} / a:unknown。
    # 单列索引让"这个人在这个活动下的提交"查询在登录与匿名两种情形下语句一致；
    # 只用 user_id + client_id 就要写 OR，那个 OR 用不上索引（design.md 决策 7）。
    submitter: Mapped[str] = mapped_column(String(80), nullable=False)

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 加盐摘要，仅用于审计；不加盐的 IPv4 摘要在数秒内可被暴力反查
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 自由标签，不做语义校验；仅用于分组与文件子目录（已消毒）
    kind: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 内容不做字段级校验，原样保存；只有"必须是 JSON 对象"与体积上限
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    # 客户端提供的幂等键；命中则返回原提交且不消耗配额
    idem_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 审核状态码。取值见 SubmissionStatus —— 整数而不是字符串，因为导出与筛选
    # 按这个码值走，而码值是**对外契约**的一部分
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, default=SubmissionStatus.RECEIVED.value
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    files: Mapped[list["SubmissionFile"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    __table_args__ = (
        # NULL 在 SQLite 与 MySQL 的唯一索引里都被视为互不相同，
        # 因此未提供幂等键的提交可以有多条
        UniqueConstraint("event_id", "idem_key"),
        Index(
            "ix_submissions_event_id_submitter_created_at",
            "event_id",
            "submitter",
            "created_at",
        ),
        Index(
            "ix_submissions_event_id_kind_created_at",
            "event_id",
            "kind",
            "created_at",
        ),
        Index("ix_submissions_event_id_created_at", "event_id", "created_at"),
        # 导出与筛选会按状态取一个活动的子集，没有它就得全表扫
        Index("ix_submissions_event_id_status", "event_id", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Submission id={self.id} event={self.event_id!r} submitter={self.submitter!r}>"


class SubmitterQuota(Base):
    """单个提交者在一个活动下已占用的份数。

    存在的理由是**原子性**，不是性能。"这个人交过几份"完全可以从 submissions
    表数出来，但那是读一下再写：在 MySQL 的可重复读下，两个并发请求会各自数到
    0，然后双双插入，**静默超限**。这里放一个能被单语句 CAS 更新的计数器，与
    活动级配额同一套理由（见 EventRepository 的配额段）。

    计数器**无论活动有没有设上限都要维护**：这样管理员以后才设上限时不必再回填
    一次，也不会出现"设上限之前交的那些不算数"。

    没有独立主键：`(event_id, submitter)` 本身就是唯一键，再给一个自增 id 只会
    多一个没人用的索引。
    """

    __tablename__ = "submitter_quotas"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    # 形态与 submissions.submitter 一致：u:{user_id} / a:{client_id}
    submitter: Mapped[str] = mapped_column(String(80), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubmitterQuota {self.event_id} {self.submitter}={self.used}>"


class SubmissionFile(Base):
    __tablename__ = "submission_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 冗余一份活动标识：下载时要用它定位 /data/{event_id}/，避免每次多一次 JOIN
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )

    # 相对 /data/{event_id}/ 的路径：{kind}/{yyyy}/{mm}/{uuid}{ext}
    # 原始文件名绝不参与路径构造（路径穿越防护）
    stored_rel: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)

    ext: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 由字节嗅探得出，不采信客户端声明的 Content-Type
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # pending -> committed；进程中断留下的 pending 由 janitor 回收
    storage_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StorageState.PENDING.value
    )

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    submission: Mapped[Submission] = relationship(back_populates="files")

    __table_args__ = (
        Index("ix_submission_files_event_id_sha256", "event_id", "sha256"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubmissionFile id={self.id} name={self.original_name!r}>"
