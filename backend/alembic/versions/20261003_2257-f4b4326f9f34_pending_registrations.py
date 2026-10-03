"""pending registrations

新建 `pending_registrations`：注册改为两阶段之后，请求先在这里占位，账号在邮箱
验证成功后才被创建（design.md 决策 19）。

这张表有两个地方与常规建表不同，都值得写清楚：

**没有外键指向 `users`。** 这一行存在的整个前提就是那个用户还不存在。

**`username` 与 `email` 各自唯一，而唯一索引不认时间。** 占位存续期间这两个槽位
被保留，但过期的行在被**真正删除**之前会一直占着它们 —— 所以"清理"不是卫生工作，
而是这套机制的一部分。删除做在两处：注册请求时先删掉与之冲突的过期行（到期即刻
释放），以及既有的 janitor 任务兜底扫描。

新建表，没有既有数据要迁移，因此不需要回填。

Revision ID: f4b4326f9f34
Revises: afbd98e64a5e
Create Date: 2026-10-03 22:57:54.310498

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b4326f9f34"
down_revision: Union[str, Sequence[str], None] = "afbd98e64a5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建待验证占位表。"""
    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.Integer(), nullable=False),
        # 与 users 同一套归一化：`Alice` 与 `alice` 必须争同一个槽位
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_registrations")),
        sa.UniqueConstraint("email", name=op.f("uq_pending_registrations_email")),
        sa.UniqueConstraint(
            "token_hash", name=op.f("uq_pending_registrations_token_hash")
        ),
        sa.UniqueConstraint("username", name=op.f("uq_pending_registrations_username")),
    )
    # 清理任务要按它扫，没有索引就是全表扫
    with op.batch_alter_table("pending_registrations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pending_registrations_expires_at"),
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    """丢掉占位表。

    尚未验证的注册会一并消失 —— 它们在两阶段设计里本来就还不是账号，重来一次即可。
    """
    with op.batch_alter_table("pending_registrations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pending_registrations_expires_at"))

    op.drop_table("pending_registrations")
