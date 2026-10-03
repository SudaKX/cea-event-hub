"""数据库引擎与会话。

引擎封装在 `Database` 对象里而不是模块级单例，这样测试可以针对临时库构造独立的
Database 并覆盖依赖，不必污染进程级状态。
"""

from __future__ import annotations

import json
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
    kwargs: dict[str, Any] = {
        "future": True,
        # JSON 列不转义非 ASCII。
        #
        # 默认的 `json.dumps` 会把中文写成 `\uXXXX`，于是：
        #   1. 对内容做文本搜索时，搜中文永远匹配不上（存的是转义形式）
        #   2. 与 `canonical_json` 不一致 —— 体积校验按**未转义**的字节数算，
        #      库里却存着更长的转义形式
        # 读回来是一样的，所以这是一次纯粹的存储形态修正。
        "json_serializer": lambda obj: json.dumps(
            obj, ensure_ascii=False, separators=(",", ":")
        ),
    }
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


def disable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite 上关掉外键约束。**只给迁移用，不要用在运行时引擎上。**

    批处理模式把每个 ALTER 重写成"建临时表 -> 拷数据 -> DROP 原表 -> 改名"。
    外键开着时，`DROP TABLE events` 会触发 `submissions` / `submission_files` /
    `submitter_quotas` 的 ON DELETE CASCADE —— 改一个活动表的列，就把所有提交
    连同附件记录清空，而且**没有任何报错**。

    必须在 connect 事件里执行：`PRAGMA foreign_keys` 在事务内是空操作。

    注册顺序有讲究：本函数要在 `register_sqlite_pragmas` **之后**调用，监听器
    按注册顺序执行，后注册的覆盖先注册的。
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=OFF")
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
