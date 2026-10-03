"""Alembic 运行环境。

`render_as_batch=True` 是必需的：SQLite 不支持绝大多数 ALTER TABLE，Alembic
必须把变更重写成"建新表 -> 拷数据 -> 换名"的批处理序列，否则任何后续迁移在
SQLite 上都会直接失败。该选项在 MySQL 上退化为普通 ALTER，无副作用。

**在 SQLite 上必须同时关掉外键约束**，见 `_disable_sqlite_foreign_keys` 的说明 ——
这是两条各自都合理的设计撞在一起的后果，代价是数据被静默清空。
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context

from app.core.config import settings
from app.db import models  # noqa: F401  导入以注册全部模型
from app.db.base import Base
from app.db.session import build_engine, disable_sqlite_foreign_keys
from app.db.types import UtcDateTime


def _render_item(type_: str, object_, autogen_context) -> str | bool:  # noqa: ANN001
    """把自定义时间类型渲染成底层的 DateTime，其余交给默认渲染。

    Alembic 对未知类型默认按"模块路径 + ()"渲染，于是生成的迁移里会出现
    `app.db.types.UtcDateTime()` 却**不带任何 import**，一运行就 NameError。

    渲染成 `sa.DateTime()` 同时解决两件事：迁移不需要额外 import，而且迁移从此
    "冻结"在生成那一刻——将来改动 UtcDateTime 的实现，历史迁移的行为不会跟着
    变。UTC 的读写语义由应用层类型负责，数据库层面它本来就是 DATETIME。

    注意：这个钩子必须经 `context.configure(render_item=...)` 传入；
    `alembic.autogenerate.renderers` 那个 Dispatcher 本身不会自动生效。
    """
    if type_ == "type" and isinstance(object_, UtcDateTime):
        return "sa.DateTime()"
    return False  # False = 交回 Alembic 默认渲染

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False：否则应用已建立的 logger 会被静默关闭，
    # 迁移后运行时的日志就没了
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# 连接串默认来自应用配置而不是 alembic.ini：URL 里可能含 % 或口令，
# 放进 ini 会被 configparser 当作插值语法解析。
#
# 但**程序化调用**（测试、脚本）要能指定别的库，所以留一个 attributes 通道 ——
# 这是 Alembic 自己的扩展点，同样绕开了 ini 的插值问题。
DATABASE_URL = config.attributes.get("database_url") or settings.DATABASE_URL


def _configure(connection=None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        # SQLite 的 ALTER 支持极弱，必须走批处理
        render_as_batch=True,
        # 检测列类型变化，否则改类型不会进 autogenerate
        compare_type=True,
        compare_server_default=True,
        # 自定义类型的渲染钩子（见文件上方说明）
        render_item=_render_item,
        # 迁移文件名按时间排序
        include_schemas=False,
    )


def _disable_sqlite_foreign_keys(engine) -> None:  # noqa: ANN001
    """SQLite 上，迁移期间关掉外键约束。

    **不关会静默删数据。** 批处理模式把每个 ALTER 重写成"建临时表 -> 拷数据 ->
    DROP 原表 -> 改名"。外键开着时，`DROP TABLE events` 会触发
    `submissions` / `submission_files` / `submitter_quotas` 的 ON DELETE CASCADE
    —— 改一个活动表的列，就把所有提交连同附件记录清空，而且没有任何报错。

    这是两条各自都合理的设计撞在一起的后果：
      - `build_engine` 统一打开外键，免得运行时与迁移拿到行为不同的连接
      - `render_as_batch=True` 是 SQLite 上做 ALTER 的唯一办法
    所以修在这里，而不是去改某个迁移：**一次修复覆盖全部历史与将来的迁移**。

    代价是迁移期间的外键不再由数据库把关。但批处理本来就在重建表，外键在这期间
    从来不是一道可靠的门 —— 真正的把关在运行时连接上（那条路径不受影响）。

    实现放在 `app.db.session` 里，好让测试能直接断言它。
    """
    disable_sqlite_foreign_keys(engine)


def run_migrations_offline() -> None:
    _configure(url=DATABASE_URL)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = build_engine(DATABASE_URL)
    if connectable.dialect.name == "sqlite":
        # 注册在 build_engine 之后：监听器按注册顺序执行，后注册的覆盖先注册的
        _disable_sqlite_foreign_keys(connectable)

    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
