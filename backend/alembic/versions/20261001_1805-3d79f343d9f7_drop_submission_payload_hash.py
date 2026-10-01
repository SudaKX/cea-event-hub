"""drop submission payload hash

移除按内容哈希做的窗口内去重。

那个机制不看请求身份、只看内容相似度，误判时会把用户的提交连同附件一起
**静默丢弃**（"字段没改、只换了一个附件"就会命中），而重复提交留下两条记录
是响亮且可恢复的。因此整条链路一起拆掉：列、索引、服务里的调用、仓储方法、
`DEDUP_WINDOW_SECONDS` 配置。

幂等键去重（`idem_key` 列与 `UniqueConstraint`）**保留**，它是客户端显式给出的
意图标识，不存在误判。

Revision ID: 3d79f343d9f7
Revises: de7cf770dbec
Create Date: 2026-10-01 18:05:52.110197

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d79f343d9f7"
down_revision: Union[str, Sequence[str], None] = "de7cf770dbec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除内容哈希列及其索引。"""
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_submissions_event_id_payload_hash"))
        batch_op.drop_column("payload_hash")


def downgrade() -> None:
    """恢复列与索引。

    建表时这一列是 NOT NULL 且没有默认值。在**已经有数据**的表上加这样的列会
    直接失败，所以这里先带一个空串默认值加上去，再把默认值摘掉 —— 这样降级在
    非空库上也能真的跑通，而不是只看起来对称。

    默认值填 `""`（而不是重算哈希）是有意的：降级恢复的是**结构**，不是那个已经
    被放弃的去重语义。真要重新启用内容去重，应当另起一次迁移并显式回填。
    """
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "payload_hash",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_submissions_event_id_payload_hash"),
            ["event_id", "payload_hash", "created_at"],
            unique=False,
        )

    # 摘掉临时默认值，让结构回到建表时的样子
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.alter_column("payload_hash", server_default=None)
