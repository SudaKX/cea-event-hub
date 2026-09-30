"""仓储层。

规则：只做持久化，**不含业务判断**。仓储回答"存了什么"，不回答"能不能存"。
方言 SQL 也只允许出现在这一层（可移植性规则第 6 条）。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Select
from sqlalchemy.orm import Session


def paginate(statement: Select, *, offset: int, limit: int) -> Select:
    """给查询语句加上分页。各仓储共用，避免每处各写一份 offset/limit。"""
    return statement.offset(max(offset, 0)).limit(max(limit, 1))


def iter_scalars(session: Session, statement: Select) -> Iterator[object]:
    return iter(session.scalars(statement).all())


__all__ = ["iter_scalars", "paginate"]
