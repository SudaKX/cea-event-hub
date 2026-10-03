"""event visibility as integer codes

把可见性从 `public` / `private` 两个字符串改成三档整数码值：

    旧字符串    新码值   含义
    private  ->  0      不进任何公开面（但仍可按标识访问）
    public   ->  1      进公开列表端点与首页的标识补全
    （新增）  ->  2      在此之上，还进首页的卡片区

**不能只改列类型。** SQLite 把 `'public'` 这样的非数字字符串转成 INTEGER 时得到
0 —— 也就是说每一条活动都会静默变成"不公开"，整站目录一夜清空。必须先把码值算
出来再换列。

Revision ID: 9f1c2a4b7e03
Revises: 8d54ac924e60
Create Date: 2026-10-03 12:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f1c2a4b7e03"
down_revision: Union[str, Sequence[str], None] = "8d54ac924e60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 旧字符串 -> 新码值。兜底给 1（公开）：加这一版之前所有 live 活动都在目录里，
#: 认不出来的一律当作公开，比悄悄从目录里消失安全。
_FORWARD = """
UPDATE events SET visibility_code = CASE visibility
    WHEN 'private' THEN 0
    WHEN 'public'  THEN 1
    ELSE 1
END
"""

#: 反向映射。码值 2（置顶）降级后只能是 public —— 旧版本没有置顶这个概念，
#: 它至少还是个公开活动。
_BACKWARD = """
UPDATE events SET visibility_legacy = CASE visibility
    WHEN 0 THEN 'private'
    ELSE 'public'
END
"""


def upgrade() -> None:
    """字符串可见性 -> 整数码值。

    顺序是**先删索引、再动列、最后重建**。反过来的话，列一被删，指着它的那个索引
    就成了悬空引用，SQLite 要么直接拒绝、要么在重建索引时才报错 —— 而那时表已经
    改了一半，错误信息指向的是索引，跟真正的原因差得很远。
    """
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_visibility")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visibility_code", sa.Integer(), nullable=True))

    # 先算好码值再动列 —— 顺序反了就会丢掉原值
    op.execute(_FORWARD)

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_column("visibility")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.alter_column(
            "visibility_code",
            new_column_name="visibility",
            existing_type=sa.Integer(),
            nullable=False,
        )

    # 索引另起一批：同一批里 alter_column 还没把新表的列改名，
    # create_index 会去找一个当时还不存在的列名
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_events_visibility"), ["visibility"], unique=False
        )


def downgrade() -> None:
    """整数码值 -> 字符串。置顶（2）降级为 public，这一点无法避免。"""
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_visibility")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("visibility_legacy", sa.VARCHAR(length=16), nullable=True)
        )

    op.execute(_BACKWARD)

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_column("visibility")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.alter_column(
            "visibility_legacy",
            new_column_name="visibility",
            existing_type=sa.VARCHAR(length=16),
            nullable=False,
        )

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_events_visibility"), ["visibility"], unique=False
        )
