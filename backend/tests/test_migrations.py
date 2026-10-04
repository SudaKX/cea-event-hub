"""迁移本身的行为。

这里的重点是**批处理模式不会连带删数据**。

SQLite 不支持绝大多数 ALTER TABLE，所以 `render_as_batch=True` 会把每个变更
重写成"建临时表 -> 拷数据 -> DROP 原表 -> 改名"。外键开着时，`DROP TABLE events`
会触发 `submissions` / `submission_files` / `submitter_quotas` 的 ON DELETE
CASCADE —— **改一个活动表的列，就把所有提交连同附件记录清空**，而且没有任何
报错。

这个 bug 真的发生过：可见性那次迁移把开发库里 26 条提交删到只剩 3 条。所以它
值得一个跑真实迁移链的测试，而不是只靠人工验证。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

#: 一条链上包含批处理 ALTER 的关键版本。逐个升过去，每一步都查数据还在不在。
#:
#: `4d594b75179e`（给 users.id 加 AUTOINCREMENT）是其中最凶的一条：它重建的
#: `users` 被五处外键引用，其中两处是 `ON DELETE CASCADE`（sessions、user_tokens）、
#: 三处是 `SET NULL`（events.created_by、submissions.reviewed_by、
#: submission_files.uploaded_by）。只数行数抓不到后者的损坏，所以 `_seed` 会把那
#: 三列都填上，`_audit_columns` 专门盯着它们不被置空。
BATCH_STEPS = ["8d54ac924e60", "9f1c2a4b7e03", "afbd98e64a5e", "4d594b75179e"]


def _config(database: Path) -> Config:
    """给这份配置指定一个独立的库。

    走 `attributes` 而不是 ini 里的 `sqlalchemy.url`：env.py 特意不从 ini 读 URL
    （口令或 % 会被 configparser 当插值解析），attributes 是 Alembic 自己的扩展点。
    """
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite+pysqlite:///{database.as_posix()}"
    return config


@pytest.fixture
def migrated_db(tmp_path) -> Path:
    """一个空的库文件。建表与塞数据都由各用例自己做。"""
    return tmp_path / "migration.db"


def _counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(database)
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "events",
                "submissions",
                "submission_files",
                # 用户侧的这两张表是 ON DELETE CASCADE：重建 users 时外键若开着，
                # 它们会被连带删掉
                "sessions",
                "user_tokens",
            )
        }
    finally:
        connection.close()


def _audit_columns(database: Path) -> tuple:
    """三处 `ON DELETE SET NULL` 的审计列。

    只数行数抓不到它们的损坏：`DROP TABLE users` 在外键开着时会把它们**清空**，
    而每一行的数量都不变。所以单独把值取出来比。

    真实列名是 `events.owner_id`、`submissions.user_id`、`submissions.reviewed_by`
    —— `submission_files` 上没有指向用户的列。
    """
    connection = sqlite3.connect(database)
    try:
        return (
            connection.execute("SELECT owner_id FROM events").fetchone()[0],
            connection.execute("SELECT user_id FROM submissions").fetchone()[0],
            connection.execute("SELECT reviewed_by FROM submissions").fetchone()[0],
        )
    finally:
        connection.close()


def _seed(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        # 先造一个用户：下面三处外键都指向它，重建 users 时它们要么被置空、要么
        # 连同会话一起被级联掉
        connection.execute(
            "INSERT INTO users (id, username, display_name, password_hash, role, "
            "is_active, created_at, updated_at) "
            "VALUES (1,'alice','Alice','x','user',1,'2026-01-01','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, "
            "last_seen_at) VALUES ('sessionhash',1,'2026-01-01','2027-01-01','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO user_tokens (user_id, purpose, token_hash, expires_at, "
            "created_at) VALUES (1,'email_verify','tokenhash','2027-01-01','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO events (id, title, status, entry_path, content_version, "
            "submission_requires_login, submission_count, owner_id, created_at, "
            "updated_at) "
            "VALUES ('e1','t','live','index.html',0,0,1,1,'2026-01-01','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO submissions (id, event_id, submitter, user_id, payload, "
            "status, reviewed_by, created_at) "
            "VALUES (1,'e1','u:1',1,'{}',1,1,'2026-01-01')"
        )
        connection.execute(
            "INSERT INTO submission_files (submission_id, event_id, original_name, "
            "stored_rel, size_bytes, sha256, mime, storage_state, created_at) "
            "VALUES (1,'e1','a.pdf','x/a.pdf',10,'deadbeef','application/pdf',"
            "'committed','2026-01-01')"
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.slow
def test_batch_migrations_do_not_delete_dependent_rows(migrated_db: Path) -> None:
    """批处理 ALTER 不能连带删掉依赖表的行，也不能把审计列清空。

    这条如果红了，说明迁移期间外键又开着 —— 表现是升级之后依赖行没了（或者那三个
    `SET NULL` 的列被清空），且**没有任何报错**，所以要靠这个测试拦住。
    """
    config = _config(migrated_db)
    # 停在批处理迁移之前
    command.upgrade(config, "7d1b16f0e498")
    _seed(migrated_db)
    expected = {
        "events": 1,
        "submissions": 1,
        "submission_files": 1,
        "sessions": 1,
        "user_tokens": 1,
    }
    assert _counts(migrated_db) == expected
    assert _audit_columns(migrated_db) == (1, 1, 1)

    for revision in BATCH_STEPS:
        command.upgrade(config, revision)
        counts = _counts(migrated_db)
        assert counts == expected, f"升到 {revision} 之后数据丢了：{counts}"
        # 行数不变也可能是被"置空"而不是被删 —— 那三个 SET NULL 的列单独比
        assert _audit_columns(migrated_db) == (1, 1, 1), (
            f"升到 {revision} 之后审计列被清空了"
        )


@pytest.mark.slow
def test_full_upgrade_and_downgrade_round_trip(migrated_db: Path) -> None:
    """整条链能升上去、也能降回来，且数据始终在。"""
    config = _config(migrated_db)
    command.upgrade(config, "7d1b16f0e498")
    _seed(migrated_db)

    command.upgrade(config, "head")
    assert _counts(migrated_db)["submissions"] == 1

    command.downgrade(config, "7d1b16f0e498")
    assert _counts(migrated_db)["submissions"] == 1

    command.upgrade(config, "head")
    assert _counts(migrated_db)["submissions"] == 1


@pytest.mark.slow
def test_migration_connection_has_foreign_keys_disabled(migrated_db: Path) -> None:
    """把修复本身钉住：迁移用的连接上，外键必须是关的。

    直接断言连接状态而不是"升级后数据还在"，是为了让失败信息指向原因 ——
    数据没了的报错很难让人联想到外键与批处理的相互作用。
    """
    from app.db.session import build_engine, disable_sqlite_foreign_keys

    engine = build_engine(f"sqlite+pysqlite:///{migrated_db.as_posix()}")
    try:
        # build_engine 默认是开的（运行时就需要它）
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

        # 迁移环境在它之后注册，覆盖成关。
        # 必须先 dispose：connect 事件只在**新建** DBAPI 连接时触发，而池子里
        # 已经有一个连接了，直接再 connect 拿到的还是它、事件不会再跑一遍。
        # 真实迁移里引擎是全新的，第一次连接必然新建，所以不存在这个问题。
        disable_sqlite_foreign_keys(engine)
        engine.dispose()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 改写既有数据的迁移
#
# 下面三条各自改写一列既有数据，而它们的验证此前只存在于 `var/` 下**不入库**的
# 脚本里 —— 也就是说"验过了"这句话在仓库里没有任何凭据。迁移改错数据的代价是
# 静默的（码值全变 0、中文全变转义序列），值得入库钉住。
# ---------------------------------------------------------------------------


def _raw(database: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(database)
    try:
        return list(connection.execute(sql))
    finally:
        connection.close()


def _exec(database: Path, *statements: str) -> None:
    connection = sqlite3.connect(database)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


@pytest.mark.slow
def test_payload_escaped_json_is_rewritten_as_readable(migrated_db: Path) -> None:
    """JSON 里被转义的中文要还原成可读形式。

    默认的 `json.dumps` 把中文写成 `\\uXXXX`，于是按内容搜索永远匹配不上 ——
    功能看起来在，实际对中文完全无效。迁移就地还原既有行。
    """
    config = _config(migrated_db)
    command.upgrade(config, "24e091511e4a")  # 去转义迁移之前
    _exec(
        migrated_db,
        "INSERT INTO events (id, title, status, entry_path, content_version, "
        "submission_requires_login, submission_count, created_at, updated_at) "
        "VALUES ('e1','t','live','index.html',0,0,1,'2026-01-01','2026-01-01')",
        # 直接写转义后的原文：这正是升级前库里的样子
        "INSERT INTO submissions (id, event_id, submitter, payload, status, created_at) "
        "VALUES (1,'e1','a:alice','{\"name\": \"\\u5f20\\u4e09\"}',1,'2026-01-01')",
    )
    assert _raw(migrated_db, "SELECT payload FROM submissions")[0][0].count("\\u") == 2

    command.upgrade(config, "head")

    stored = _raw(migrated_db, "SELECT payload FROM submissions")[0][0]
    assert "张三" in stored
    assert "\\u5f20" not in stored


@pytest.mark.slow
def test_visibility_strings_become_integer_codes(migrated_db: Path) -> None:
    """字符串可见性要按语义映射成码值，不能只改列类型。

    SQLite 把 `'public'` 这样的非数字字符串转成 INTEGER 得到 **0** —— 只改类型的话
    每一条活动都会静默变成"不公开"，整站目录一夜清空。所以迁移必须先把码值算出来。
    """
    config = _config(migrated_db)
    command.upgrade(config, "8d54ac924e60")  # 可见性还是字符串的那一版
    _exec(
        migrated_db,
        "INSERT INTO events (id, title, status, visibility, entry_path, "
        "content_version, submission_requires_login, submission_count, "
        "created_at, updated_at) VALUES "
        "('hidden','t','live','private','index.html',0,0,0,'2026-01-01','2026-01-01')",
        "INSERT INTO events (id, title, status, visibility, entry_path, "
        "content_version, submission_requires_login, submission_count, "
        "created_at, updated_at) VALUES "
        "('shown','t','live','public','index.html',0,0,0,'2026-01-01','2026-01-01')",
    )

    command.upgrade(config, "9f1c2a4b7e03")

    codes = dict(_raw(migrated_db, "SELECT id, visibility FROM events"))
    assert codes == {"hidden": 0, "shown": 1}


@pytest.mark.slow
def test_submitter_counters_are_backfilled(migrated_db: Path) -> None:
    """计数器要按既有提交回填。

    不回填的话，管理员把活动设成"每人最多 1 份"时，一个已经交过 3 份的人在计数器里
    没有行 —— 下一次提交会走"行不存在 → 插入 1"这条路直接成功，超限静默通过。
    """
    config = _config(migrated_db)
    command.upgrade(config, "9f1c2a4b7e03")  # 计数器表还不存在
    _exec(
        migrated_db,
        "INSERT INTO events (id, title, status, visibility, entry_path, "
        "content_version, submission_requires_login, submission_count, "
        "created_at, updated_at) VALUES ('e1','t','live',1,'index.html',0,0,0,"
        "'2026-01-01','2026-01-01')",
        "INSERT INTO submissions (event_id, submitter, payload, status, created_at) "
        "VALUES ('e1','a:alice','{}',1,'2026-01-01'), "
        "('e1','a:alice','{}',1,'2026-01-01'), "
        "('e1','a:alice','{}',1,'2026-01-01'), "
        "('e1','a:bob','{}',1,'2026-01-01')",
    )

    command.upgrade(config, "afbd98e64a5e")

    counters = dict(_raw(migrated_db, "SELECT submitter, used FROM submitter_quotas"))
    # 从没交过的人不该有行 —— 有行就等于凭空占了一份
    assert counters == {"a:alice": 3, "a:bob": 1}
