"""数据库引擎与会话。

引擎封装在 `Database` 对象里而不是模块级单例，这样测试可以针对临时库构造独立的
Database 并覆盖依赖，不必污染进程级状态。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


def is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def create_db_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if is_sqlite_url(url):
        # FastAPI 的同步端点跑在线程池里，连接会被不同线程借出借入
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            # 内存库必须复用同一条连接，否则每条连接看到的是各自独立的库
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(url, **kwargs)


def register_sqlite_pragmas(engine: Engine) -> None:
    """安装连接级 PRAGMA。

    SQLite 的外键约束**默认是关闭的**（可移植性规则第 4 条）。不打开它，
    `ON DELETE CASCADE` 会静默失效并留下孤儿行——删除提交时附件记录不会跟着
    消失，而 MySQL 上同样的 schema 行为却正确，属于最难查的一类差异。
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def build_engine(url: str) -> Engine:
    """按 URL 构造引擎，并挂上该方言需要的连接级设置。

    Alembic 与应用共用这一个入口，避免迁移时与运行时拿到行为不同的连接
    （例如迁移期间外键约束是关的，会放过本应被拒的数据）。
    """
    engine = create_db_engine(url)
    if is_sqlite_url(url):
        register_sqlite_pragmas(engine)
    return engine


class Database:
    """一个数据库的引擎 + 会话工厂。"""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = build_engine(url)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        """事务边界：正常提交，异常回滚。

        service 层用它包裹需要原子性的多步写入（例如配额 CAS + 插入提交）。
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_all(self) -> None:
        """按 ORM 元数据建表。仅供测试与本地快速起步；生产走 Alembic。"""
        from app.db import models  # noqa: F401  导入以注册全部模型
        from app.db.base import Base

        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        from app.db import models  # noqa: F401
        from app.db.base import Base

        Base.metadata.drop_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


# 应用级默认实例；测试通过依赖覆盖注入自己的 Database
db = Database(settings.DATABASE_URL)
