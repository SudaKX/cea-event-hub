"""per-submitter submission limit

给活动加"单个提交者最多几份"，并建配套的份数计数器。

**必须回填既有计数。** 不回填的话，管理员把某个活动设成"每人最多 1 份"，而某个
人此前已经交过 3 份 —— 计数器里没有他的行，下一次提交会走"行不存在 → 插入
used=1"这条路**直接成功**，超限静默通过。回填之后计数从真实行数出发，第一次
CAS 就会正确地把他挡住。

计数从 submissions 表按 (event_id, submitter) 分组数出来，与运行时维护它的口径
一致（见 SubmitterQuota 的说明）。

Revision ID: afbd98e64a5e
Revises: 9f1c2a4b7e03
Create Date: 2026-10-03 15:18:04.297875

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "afbd98e64a5e"
down_revision: Union[str, Sequence[str], None] = "9f1c2a4b7e03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 既有提交按提交者归组计数。INSERT ... SELECT 是一条语句，不必把行读进内存。
_BACKFILL = """
INSERT INTO submitter_quotas (event_id, submitter, used)
SELECT event_id, submitter, COUNT(*)
FROM submissions
GROUP BY event_id, submitter
"""


def upgrade() -> None:
    """建计数器表与活动字段，并按既有提交回填计数。"""
    op.create_table(
        "submitter_quotas",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("submitter", sa.String(length=80), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_submitter_quotas_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", "submitter", name=op.f("pk_submitter_quotas")),
    )
    # 可空 = 不限制，与加这个字段之前的行为一致，因此无需为它回填
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("max_per_submitter", sa.Integer(), nullable=True))

    op.execute(_BACKFILL)


def downgrade() -> None:
    """去掉计数器与活动字段。

    计数器的内容是可从 submissions 重算出来的派生数据，因此降级直接丢弃它，
    不需要保留任何东西。
    """
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_column("max_per_submitter")

    op.drop_table("submitter_quotas")
