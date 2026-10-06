"""invitation codes

邀请码准入：三张新表，以及待验证占位上指向邀请码的外键。

**为什么占位需要那个外键。** 准入在注册第一步判定，而邀请码的次数要到第二步
（点邮件核销建号）才扣 —— 那时用户手里只有邮件里的令牌，没有那串码，所以码必须
在第一阶段就记在占位上。

**这一步会重建 `pending_registrations`。** SQLite 不能给既有表加外键，只能建新表、
搬数据、换名字 —— 也就是批处理模式做的事。重建期间`invitation_code_id` 一律为
NULL（升级前建立的占位本来就没有码），而**那些占位必须一条不少地活下来**：准入是在
提交注册那一刻判定的，不该回头把已经在等邮件的人挡掉。因此这个版本加入了
`tests/test_migrations.py` 的 `BATCH_STEPS` 名单。

**只加表、加可空列，没有回填**，也没有改动既有列。

Revision ID: cb1a959c4b48
Revises: 4d594b75179e
Create Date: 2026-10-05 16:20:56.164683

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cb1a959c4b48"
down_revision: Union[str, Sequence[str], None] = "4d594b75179e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建邀请码两张表与平台开关表，并给占位加上指向邀请码的外键。"""
    op.create_table(
        "invitation_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        # 用户删除自己未使用的码 —— 软删，因为物理删除会抹掉 24 小时间隔的证据
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # SET NULL：发码人注销时，码与"他邀请过的人"都不该被牵连（见模型里的说明）
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_invitation_codes_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitation_codes")),
        sa.UniqueConstraint("token", name=op.f("uq_invitation_codes_token")),
    )
    with op.batch_alter_table("invitation_codes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_invitation_codes_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_invitation_codes_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_invitation_codes_owner_id"), ["owner_id"], unique=False
        )

    op.create_table(
        "platform_switches",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_platform_switches_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_platform_switches")),
    )

    op.create_table(
        "invitation_redemptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("used_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["code_id"],
            ["invitation_codes.id"],
            name=op.f("fk_invitation_redemptions_code_id_invitation_codes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_invitation_redemptions_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitation_redemptions")),
    )
    with op.batch_alter_table("invitation_redemptions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_invitation_redemptions_code_id"), ["code_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_invitation_redemptions_user_id"), ["user_id"], unique=False
        )

    # 这一步在 SQLite 上会重建 pending_registrations（加外键只能这么加）
    with op.batch_alter_table("pending_registrations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("invitation_code_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_pending_registrations_invitation_code_id_invitation_codes"),
            "invitation_codes",
            ["invitation_code_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """退回没有邀请码的状态。

    **正在等待验证的占位会丢掉它们的邀请码**（那一列被删掉），但占位本身保留 ——
    降级之后准入不再校验，那些占位仍可正常核销。
    """
    with op.batch_alter_table("pending_registrations", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_pending_registrations_invitation_code_id_invitation_codes"),
            type_="foreignkey",
        )
        batch_op.drop_column("invitation_code_id")

    with op.batch_alter_table("invitation_redemptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_invitation_redemptions_user_id"))
        batch_op.drop_index(batch_op.f("ix_invitation_redemptions_code_id"))

    op.drop_table("invitation_redemptions")
    op.drop_table("platform_switches")

    with op.batch_alter_table("invitation_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_invitation_codes_owner_id"))
        batch_op.drop_index(batch_op.f("ix_invitation_codes_expires_at"))
        batch_op.drop_index(batch_op.f("ix_invitation_codes_created_at"))

    op.drop_table("invitation_codes")
