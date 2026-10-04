"""users id does not reuse deleted ids

给 `users.id` 加上 SQLite 的 `AUTOINCREMENT` 关键字。

**为什么必须有它。** 不写这个关键字时 `INTEGER PRIMARY KEY` 是 rowid 的别名，新行的
id 取 `max(id)+1` —— 于是删掉 id 最大的那一行之后，下一条插入会**拿回同一个 id**。
而 `submissions.submitter` 是派生字符串 `u:{user_id}`、没有外键，因此新注册的人会从
数据角度"继承"前一个被删者的提交与配额计数，且没有任何报错（design.md 决策 3）。
"删除账号"这个功能以它为前提。

**为什么是重建表。** SQLite 没有 `ALTER TABLE ... AUTOINCREMENT`，只能建新表、搬数据、
换名字 —— 也就是 Alembic 批处理模式做的事。而 `users` 被**五处外键**引用
（sessions、user_tokens、events.created_by、submissions.reviewed_by、
submission_files.uploaded_by），所以这条迁移正落在"批处理 + 外键打开 = 依赖行被级联
删掉"那个坑上。修复在 `alembic/env.py` 的 `disable_sqlite_foreign_keys`，回归在
`tests/test_migrations.py` 的 `BATCH_STEPS` —— 本迁移的版本号必须在那份名单里。

**只改 id 的分配策略，数据一字不动**，因此没有回填。

Revision ID: 4d594b75179e
Revises: f4b4326f9f34
Create Date: 2026-10-04 23:08:12.103442

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d594b75179e"
down_revision: Union[str, Sequence[str], None] = "f4b4326f9f34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _upgrade_sql() -> str:
    """重建 `users` 的 DDL，唯一区别是 id 上的 `AUTOINCREMENT`。

    列与约束照迁移写下时的形态硬编码，不 import 模型：迁移是快照，模型会继续演化。
    约束名必须与 `app/db/base.py` 的命名约定一致，否则重建之后约束会改名。

    **主键只能内联。** SQLite 要求 `AUTOINCREMENT` 紧跟在列上的 `PRIMARY KEY` 之后，
    此时不能再写表级的 `PRIMARY KEY (id)`（否则报 "more than one primary key"）。
    内联主键没有名字，但 SQLite 本来就忽略约束名，`pk_users` 这个名字来自 SQLAlchemy
    的元数据而非数据库 —— 因此这条只对 SQLite 生效的重建不影响命名约定。
    """
    return """
    CREATE TABLE users_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        username VARCHAR(64) NOT NULL,
        display_name VARCHAR(64) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        email VARCHAR(255),
        email_verified_at DATETIME,
        role VARCHAR(16) NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_users_username UNIQUE (username),
        CONSTRAINT uq_users_email UNIQUE (email)
    )
    """


def _downgrade_sql() -> str:
    """退回不带 `AUTOINCREMENT` 的写法。

    这一份刻意与迁移前的原始 DDL 一致（表级 `PRIMARY KEY (id)`），使降级后的形态与
    升级前**逐字相同**，而不是"另一种也能用"的形态 —— 降级要能回到起点。
    """
    return """
    CREATE TABLE users_new (
        id INTEGER NOT NULL,
        username VARCHAR(64) NOT NULL,
        display_name VARCHAR(64) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        email VARCHAR(255),
        email_verified_at DATETIME,
        role VARCHAR(16) NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT pk_users PRIMARY KEY (id),
        CONSTRAINT uq_users_username UNIQUE (username),
        CONSTRAINT uq_users_email UNIQUE (email)
    )
    """


COLUMNS = (
    "id, username, display_name, password_hash, email, email_verified_at, "
    "role, is_active, created_at, updated_at"
)


def _rebuild(*, create_sql: str) -> None:
    """建新表 → 搬数据 → 删旧表 → 改名。

    **不用 `batch_alter_table` 的 `copy_from`。** 试过：它按反射重建，表级 dialect
    kwarg（`sqlite_autoincrement`）在那条路径上会丢掉 —— 迁移跑成功、数据一条不少、
    `sqlite_master` 里却没有 `AUTOINCREMENT`，于是 id 照旧被复用。这正是"迁移看起来
    成功而目的没达到"的那类失败，所以这里手写 DDL，把关键字写在明面上。

    只对 SQLite 生效：`AUTO_INCREMENT` 是 MySQL 的原生行为，那边无需任何改动，因此
    在别的方言上这一步是空操作。
    """
    if op.get_bind().dialect.name != "sqlite":
        return

    op.execute(create_sql)
    op.execute(f"INSERT INTO users_new ({COLUMNS}) SELECT {COLUMNS} FROM users")
    op.execute("DROP TABLE users")
    op.execute("ALTER TABLE users_new RENAME TO users")


def upgrade() -> None:
    """让 id 不再被复用。"""
    _rebuild(create_sql=_upgrade_sql())


def downgrade() -> None:
    """退回 id 可复用的状态。

    **降级会把风险真的带回来**：降级之后删掉 id 最大的用户，下一个注册的人就会拿到
    同一个 id，并"继承"前者的提交与配额。若已经用过删除账号功能，降级前请确认没有
    悬空的 `u:{id}`。
    """
    _rebuild(create_sql=_downgrade_sql())
