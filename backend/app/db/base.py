"""声明式基类与约束命名约定。

**命名约定是 MySQL 迁移的地基**（可移植性规则第 1 条）：SQLite 忽略约束名，
而 MySQL 必须有名字才能 drop/改约束。没有统一约定时，Alembic autogenerate
会在换库后产生一堆无法回滚的匿名约束。
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# %(column_0_N_name)s 会拼接参与约束的全部列名，因此复合唯一约束也能得到
# 稳定且可读的名字。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
