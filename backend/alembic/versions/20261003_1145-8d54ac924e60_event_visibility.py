"""event visibility

给活动加"是否进公开目录"的标记。

**既有活动一律回填为 `public`** —— 加这个字段之前，所有 live 活动都出现在公开
列表里；默认成 private 会让它们一夜之间从目录里消失，而且是静默的。

`server_default` 只在加列时用一次，随后立刻去掉：留着它，新建的行会绕过应用层的
默认值，两处默认值各说各话。加列时必须给，否则非空列在已有数据的表上加不上去
（SQLite 直接报 "Cannot add a NOT NULL column with default value NULL"）。

Revision ID: 8d54ac924e60
Revises: 7d1b16f0e498
Create Date: 2026-10-03 11:45:34.764346

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d54ac924e60"
down_revision: Union[str, Sequence[str], None] = "7d1b16f0e498"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """加可见性列，既有活动全部为 public。"""
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.String(length=16),
                nullable=False,
                server_default="public",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_events_visibility"), ["visibility"], unique=False
        )

    # 默认值只在回填那一刻需要，之后由应用层给
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.alter_column("visibility", server_default=None)


def downgrade() -> None:
    """去掉可见性列 —— 私有活动会重新出现在公开列表里。"""
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_events_visibility"))
        batch_op.drop_column("visibility")
