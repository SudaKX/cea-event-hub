"""文档与代码的一致性检查。

防止文档里写的配置项在 `core/config.py` 里并不存在——这类漂移不会让任何测试
失败，只会让照着文档操作的人卡住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"

# 反引号里的全大写标识符，形如 ADMIN_INITIAL_PASSWORD
_SETTING_TOKEN = re.compile(r"`([A-Z][A-Z0-9_]{4,})`")

# 文档中会出现的、但不是配置项的全大写词
NOT_SETTINGS = {
    "HTTP",
    "HTTPS",
    "JSON",
    "SQL",
    "SQLITE",
    "MYSQL",
    "URL",
    "URLS",
    "UUID",
    "UTC",
    "CORS",
    "CSRF",
    "XSS",
    "NFC",
    "TLS",
    "DNS",
    "API",
    "SPA",
    "HTML",
    "CSS",
    "SDK",
    "IPV4",
    "README",
    "GET",
    "POST",
    "PATCH",
    "DELETE",
    "PUT",
    "OK",
    "NONE",
    "NULL",
    "TODO",
    "XFF",
    "TTL",
    "WAL",
    "PRAGMA",
    "SHA",
    "MIME",
    "IFRAME",
    "POSTMESSAGE",
    "SQLALCHEMY",
    "ALEMBIC",
    "UVICORN",
    "NGINX",
    "SYSTEMD",
    "SQLITE3",
    "JSONL",
    "DEFAULT",
    "PENDING",
    "COMMITTED",
    "LOGIN_REQUIRED",
    "EVENT_CLOSED",
    "QUOTA_EXHAUSTED",
    "RATE_LIMITED",
    "VALIDATION_FAILED",
    # 桥接层的常量：它们是前端 protocol.ts 里的名字，不是后端配置项。
    # 文档里必然出现，但不该要求它们在 core/config.py 里有对应字段。
    "STORAGE_MAX_LENGTH",
}


def _docs_files() -> list[Path]:
    """所有会对使用者描述配置项的文档。"""
    files = sorted(DOCS_DIR.glob("*.md"))
    backend_readme = REPO_ROOT / "backend" / "README.md"
    if backend_readme.exists():
        files.append(backend_readme)
    return files


def test_docs_directory_exists_and_has_content() -> None:
    files = _docs_files()
    assert files, f"{DOCS_DIR} 下没有文档"


@pytest.mark.parametrize("doc", _docs_files(), ids=lambda p: p.name)
def test_documented_settings_exist(doc: Path) -> None:
    """文档里反引号标注的全大写标识符必须真的是一个配置项。"""
    tokens = set(_SETTING_TOKEN.findall(doc.read_text(encoding="utf-8")))
    unknown = {
        token
        for token in tokens
        if token not in NOT_SETTINGS and token not in Settings.model_fields
    }
    assert not unknown, (
        f"{doc.name} 提到了 {sorted(unknown)}，但 core/config.py 里没有这些配置项"
    )
