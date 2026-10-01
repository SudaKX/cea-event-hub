"""submission status as integer codes

把提交状态从字符串改成整数码值，并去掉"审核中"这一档。

    旧字符串      新码值   理由
    received  ->  1       待处理
    reviewing ->  1       本来就没决定，归入待处理
    accepted  ->  2       已采用
    rejected  ->  0       不采用（概念上被 ignored 取代）

**不能只改列类型。** SQLite 在把非数字字符串转成 INTEGER 时得到 0，所以
`alter_column(type_=Integer())` 会把每一条提交都静默变成"不采用" —— 数据看起来
还在，含义已经全错。必须先把码值算出来再换列。

Revision ID: 24e091511e4a
Revises: 3d79f343d9f7
Create Date: 2026-10-01 23:07:24.243257

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "24e091511e4a"
down_revision: Union[str, Sequence[str], None] = "3d79f343d9f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 旧字符串 -> 新码值。兜底给 1（待处理）而不是 0：认不出来的一律当作"还没处理"，
#: 比悄悄归入"不采用"安全 —— 后者会让管理员以为已经决定过了。
_FORWARD = """
UPDATE submissions SET status_code = CASE status
    WHEN 'accepted'  THEN 2
    WHEN 'rejected'  THEN 0
    WHEN 'received'  THEN 1
    WHEN 'reviewing' THEN 1
    ELSE 1
END
"""

#: 反向映射。三档各自对应一个语义最接近的旧值。
_BACKWARD = """
UPDATE submissions SET status_legacy = CASE status
    WHEN 0 THEN 'rejected'
    WHEN 2 THEN 'accepted'
    ELSE 'received'
END
"""


def upgrade() -> None:
    """字符串状态 -> 整数码值。

    拆成五个批次而不是一个，每一步只做一件事。踩过两个坑：

    - 同一批里"删掉 status"再加"把 status_code 改名成 status"会让索引记账找不到列
    - 同一批里改名再建索引，`create_index` 会去找改名**之前**的列名

    SQLite 的批次模式会重建整表，分开最稳；MySQL 上批次模式是直通，多拆几次只是
    多发几条 ALTER。
    """
    # 1. 加一列临时整数列
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status_code", sa.Integer(), nullable=True))

    # 2. 先算好码值再动列 —— 顺序反了就会丢掉原值
    op.execute(_FORWARD)

    # 3. 丢掉旧列
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.drop_column("status")

    # 4. 改名收尾
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.alter_column(
            "status_code",
            new_column_name="status",
            existing_type=sa.Integer(),
            nullable=False,
        )

    # 5. 索引必须**另起一批**：同一批里 `alter_column(new_column_name=...)` 还没把
    #    新表的列改名，`create_index` 就会去找一个当时还不存在的列名
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_submissions_event_id_status", ["event_id", "status"], unique=False
        )


def downgrade() -> None:
    """整数码值 -> 字符串状态。

    映射是有损的（1 同时来自 received 与 reviewing），这一点无法避免：
    reviewing 这一档已经被移除，降级只能回到最接近的那一个。
    """
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.drop_index("ix_submissions_event_id_status")
        batch_op.add_column(
            sa.Column("status_legacy", sa.VARCHAR(length=16), nullable=True)
        )

    op.execute(_BACKWARD)

    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.drop_column("status")

    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.alter_column(
            "status_legacy",
            new_column_name="status",
            existing_type=sa.VARCHAR(length=16),
            nullable=False,
        )
