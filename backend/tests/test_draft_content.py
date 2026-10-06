"""草稿活动内容的开发模式挂载（add-develop-harness 任务 1.3 - 1.4）。

这一组用例要钉住的核心不是"能取到文件"，而是**草稿页与线上页面对宿主呈现出同一组
能力**：通配跨源许可头、桥接脚本就位、越界被挡。少了任何一条，本地验证都证明不了
线上的行为 —— 而那正是这套东西存在的理由。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings as global_settings
from app.main import create_app

DRAFT = "/draft"

_PAGE = b"<html><head></head><body>draft-body</body></html>"
_PAGE_WITH_SDK = (
    b'<html><head><script src="/sdk/v1/cea.js" id="cea-sdk"></script>'
    b"</head><body>draft-body</body></html>"
)


def _write_draft(content_root: Path, name: str, filename: str, data: bytes) -> None:
    target = content_root / "events" / name / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _write_content(content_root: Path, event_id: str, filename: str, data: bytes) -> None:
    target = content_root / "content" / event_id / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


class TestDevelopmentMount:
    def test_serves_draft_file(self, client, content_root) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)

        response = client.get(f"{DRAFT}/demo/index.html")

        assert response.status_code == 200
        assert "draft-body" in response.text

    def test_nested_assets_are_served(self, client, content_root) -> None:
        """草稿页要能取到自己目录下的数据文件 —— 这是文档鼓励的用法。"""
        _write_draft(content_root, "demo", "assets/notes.json", b'{"n": 1}')

        response = client.get(f"{DRAFT}/demo/assets/notes.json")

        assert response.status_code == 200
        assert response.json() == {"n": 1}

    def test_carries_wildcard_cors(self, client, content_root) -> None:
        """与 /content 同一条不对称策略：内容带通配头，接口一个头都不带。

        少了这个头，处于不透明源的草稿页连自己目录下的 JSON 都取不到。
        """
        _write_draft(content_root, "demo", "index.html", _PAGE)

        response = client.get(f"{DRAFT}/demo/index.html")

        assert response.headers["access-control-allow-origin"] == "*"

    def test_injects_sdk_when_page_does_not_reference_it(
        self, client, content_root
    ) -> None:
        """作者不写那一行也要能握手，否则症状是"活动页连不上宿主"。"""
        _write_draft(content_root, "demo", "index.html", _PAGE)

        response = client.get(f"{DRAFT}/demo/index.html")

        assert response.text.count('id="cea-sdk"') == 1

    def test_does_not_duplicate_an_existing_reference(
        self, client, content_root
    ) -> None:
        """注入是幂等的：页面自己引用了就不再插一个（插两个会让 SDK 加载两次）。"""
        _write_draft(content_root, "demo", "index.html", _PAGE_WITH_SDK)

        response = client.get(f"{DRAFT}/demo/index.html")

        assert response.text.count('id="cea-sdk"') == 1

    def test_missing_file_is_404(self, client, content_root) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)

        assert client.get(f"{DRAFT}/demo/nope.html").status_code == 404

    def test_unknown_draft_directory_is_404(self, client) -> None:
        assert client.get(f"{DRAFT}/no-such-draft/index.html").status_code == 404


class TestDraftAndContentDoNotShadowEachOther:
    """两个目录职责不同，因此**互不遮蔽**。

    若开发模式下 /content 优先读草稿目录，zip 投放的验证就会被草稿盖住 ——
    "我投了怎么没生效"会变成常规困惑，而它极难自查。这两条断言把那个可能性关掉。
    """

    def test_draft_is_not_served_under_content(self, client, content_root) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)

        assert client.get("/content/demo/index.html").status_code == 404

    def test_content_is_not_served_under_draft(self, client, content_root) -> None:
        _write_content(content_root, "spring-2026", "index.html", _PAGE)

        assert client.get(f"{DRAFT}/spring-2026/index.html").status_code == 404


class TestTraversalProtection:
    @pytest.mark.parametrize(
        "path",
        [
            f"{DRAFT}/demo/../../../etc/passwd",
            f"{DRAFT}/demo/..%2f..%2fsecret.txt",
            f"{DRAFT}/demo/%2e%2e/%2e%2e/secret.txt",
        ],
    )
    def test_escape_attempts_are_404(self, client, content_root, path) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)
        (content_root / "secret.txt").write_text("top-secret")

        response = client.get(path)

        assert response.status_code == 404
        assert "top-secret" not in response.text

    def test_encoded_traversal_cannot_escape_draft_root(
        self, client, content_root
    ) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)
        (content_root / "outside.txt").write_text("outside-content")

        response = client.get(f"{DRAFT}/demo/%2e%2e%2foutside.txt")

        assert response.status_code == 404
        assert "outside-content" not in response.text


class TestOutsideDevelopment:
    """非开发模式下这个前缀**根本不存在**。

    这一条比"返回 404"更强：它要求的是挂载本身没有发生，而不是挂上了再拒。
    """

    def test_prefix_is_not_mounted(self, app, monkeypatch, content_root) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)
        monkeypatch.setattr(global_settings, "APP_ENV", "production")

        application = create_app()

        mounted = {route.path for route in application.routes if hasattr(route, "path")}
        assert "/draft" not in mounted

    def test_requests_do_not_reach_draft_content(
        self, app, monkeypatch, content_root
    ) -> None:
        _write_draft(content_root, "demo", "index.html", _PAGE)
        monkeypatch.setattr(global_settings, "APP_ENV", "production")

        application = create_app()
        application.state.database = app.state.database

        with TestClient(application) as non_dev_client:
            response = non_dev_client.get(f"{DRAFT}/demo/index.html")

        assert response.status_code == 404
        assert "draft-body" not in response.text
