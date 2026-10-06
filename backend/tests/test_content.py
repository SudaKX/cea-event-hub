"""任务 7.1 - 7.5：内容托管、缓存与 CORS、投放防护、数据目录私有性。"""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from app.core.config import settings as global_settings
from app.core.enums import EventStatus
from app.db.models import Event

API = "/api/v1"
ADMIN = f"{API}/admin/events"


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _zip_with_symlink(name: str, target: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo(name)
        # unix 模式的高 16 位是文件类型；S_IFLNK 表示符号链接
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, target)
    return buffer.getvalue()


def _zip_with_traversal(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo(name)
        archive.writestr(info, b"pwned")
    return buffer.getvalue()


def _deploy(admin_client, event_id: str, payload: bytes, filename: str = "content.zip"):
    return admin_client.post(
        f"{ADMIN}/{event_id}/content",
        files={"file": (filename, payload, "application/zip")},
    )


def _write_content(content_root: Path, event_id: str, name: str, data: bytes) -> None:
    target = content_root / "content" / event_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


class TestStaticServing:
    """任务 7.1"""

    def test_serves_a_file(self, client, test_db, content_root) -> None:
        _seed_event(test_db)
        _write_content(content_root, "spring-2026", "index.html", b"<h1>hi</h1>")

        response = client.get("/content/spring-2026/index.html")
        assert response.status_code == 200
        # 原内容原样保留；返回的 HTML 里还会多一行注入的桥接脚本，
        # 因此这里断言"包含"而不是"逐字节相等"（见 test_content_inject.py）
        assert "<h1>hi</h1>" in response.text

    def test_content_carries_wildcard_cors(self, client, test_db, content_root) -> None:
        """不透明源的沙箱活动页对同主机也算跨源，没有这个头它取不到自己的数据。"""
        _seed_event(test_db)
        _write_content(content_root, "spring-2026", "data.json", b'{"a":1}')

        response = client.get("/content/spring-2026/data.json")
        assert response.headers["access-control-allow-origin"] == "*"

    def test_api_carries_no_cors_headers(self, client) -> None:
        """不对称的另一半：/api 一旦允许跨源，桥接代理就不再是物理边界。"""
        headers = {k.lower() for k in client.get(f"{API}/health").headers}
        assert "access-control-allow-origin" not in headers
        assert "access-control-allow-credentials" not in headers

    def test_unversioned_requests_revalidate(self, client, test_db, content_root) -> None:
        """子资源不继承入口页的 ?v=，长期缓存会让更新不生效。"""
        _seed_event(test_db)
        _write_content(content_root, "spring-2026", "style.css", b"body{}")

        response = client.get("/content/spring-2026/style.css")
        assert response.headers["cache-control"] == "no-cache"

    def test_versioned_requests_are_immutable(self, client, test_db, content_root) -> None:
        _seed_event(test_db)
        _write_content(content_root, "spring-2026", "style.css", b"body{}")

        response = client.get("/content/spring-2026/style.css?v=3")
        assert "immutable" in response.headers["cache-control"]
        assert "max-age=31536000" in response.headers["cache-control"]

    def test_unrelated_query_param_does_not_grant_long_cache(
        self, client, test_db, content_root
    ) -> None:
        # 只认 v：否则 ?t=<时间戳> 这类击穿参数会意外拿到长期缓存
        _seed_event(test_db)
        _write_content(content_root, "spring-2026", "style.css", b"body{}")

        response = client.get("/content/spring-2026/style.css?t=123")
        assert response.headers["cache-control"] == "no-cache"

    def test_missing_file_is_404(self, client, test_db) -> None:
        _seed_event(test_db)
        assert client.get("/content/spring-2026/nope.html").status_code == 404

    def test_etag_is_present_so_revalidation_is_cheap(
        self, client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        _write_content(content_root, "spring-2026", "index.html", b"<h1>hi</h1>")
        assert client.get("/content/spring-2026/index.html").headers.get("etag")


class TestTraversalProtection:
    """任务 7.2"""

    @pytest.mark.parametrize(
        "path",
        [
            "/content/spring-2026/../../../etc/passwd",
            "/content/spring-2026/..%2f..%2fsecret.txt",
            "/content/spring-2026/%2e%2e/%2e%2e/secret.txt",
        ],
    )
    def test_escape_attempts_are_404(self, client, test_db, content_root, path) -> None:
        _seed_event(test_db)
        (content_root / "secret.txt").write_text("top-secret")

        response = client.get(path)
        assert response.status_code == 404
        assert "top-secret" not in response.text

    def test_encoded_traversal_cannot_escape_content_root(
        self, client, test_db, content_root
    ) -> None:
        """防线是"不能逃出内容根"，而不是活动之间互相不可见。"""
        _seed_event(test_db, "spring-2026")
        (content_root / "outside.txt").write_text("outside-content")

        response = client.get("/content/spring-2026/%2e%2e%2foutside.txt")
        assert response.status_code == 404
        assert "outside-content" not in response.text

    def test_content_is_public_across_events_by_design(
        self, client, test_db, content_root
    ) -> None:
        """活动内容本来就是公开只读的。

        因此"能读到别的活动的内容"不是漏洞——这也正是活动页里不能放任何秘密的
        原因（见活动页接入指南）。
        """
        _seed_event(test_db, "autumn-2026")
        _write_content(content_root, "autumn-2026", "page.html", b"autumn-content")

        assert client.get("/content/autumn-2026/page.html").status_code == 200


class TestContentDeployment:
    """任务 7.3"""

    def test_successful_deploy_bumps_version(
        self, admin_client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        response = _deploy(
            admin_client,
            "spring-2026",
            _zip_bytes({"index.html": b"<h1>v1</h1>", "style.css": b"body{}"}),
        )

        assert response.status_code == 201
        body = response.json()
        assert body["file_count"] == 2
        assert body["content_version"] == 1

        assert (
            content_root / "content" / "spring-2026" / "index.html"
        ).read_text() == "<h1>v1</h1>"

    def test_redeploy_replaces_the_directory(
        self, admin_client, test_db, content_root
    ) -> None:
        """投放是覆盖而不是合并：旧包里独有的文件必须消失。"""
        _seed_event(test_db)
        _deploy(admin_client, "spring-2026", _zip_bytes({"a.txt": b"a", "old.txt": b"x"}))
        _deploy(admin_client, "spring-2026", _zip_bytes({"a.txt": b"a2"}))

        event_dir = content_root / "content" / "spring-2026"
        assert not (event_dir / "old.txt").exists()
        assert (event_dir / "a.txt").read_bytes() == b"a2"

    def test_nested_directories_are_preserved(
        self, admin_client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        _deploy(
            admin_client,
            "spring-2026",
            _zip_bytes({"index.html": b"x", "assets/img/logo.svg": b"<svg/>"}),
        )
        assert (
            content_root / "content" / "spring-2026" / "assets" / "img" / "logo.svg"
        ).exists()

    def test_traversal_entry_is_rejected_and_target_untouched(
        self, admin_client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        _deploy(admin_client, "spring-2026", _zip_bytes({"index.html": b"original"}))

        response = _deploy(
            admin_client, "spring-2026", _zip_with_traversal("../evil.txt")
        )
        assert response.status_code == 422

        # 目标目录完全没被改动，版本号也没变
        assert (
            content_root / "content" / "spring-2026" / "index.html"
        ).read_bytes() == b"original"
        assert not (content_root / "evil.txt").exists()
        assert (
            admin_client.get(f"{ADMIN}/spring-2026").json()["event"]["content_version"]
            == 1
        )

    def test_absolute_path_entry_is_rejected(
        self, admin_client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        response = _deploy(
            admin_client, "spring-2026", _zip_with_traversal("/etc/pwned")
        )
        assert response.status_code == 422
        assert not (content_root / "content" / "spring-2026").exists()

    def test_symlink_entry_is_rejected(self, admin_client, test_db, content_root) -> None:
        """否则上传者能用链接把 /data 或宿主任意文件读出去。"""
        _seed_event(test_db)
        response = _deploy(
            admin_client,
            "spring-2026",
            _zip_with_symlink("link.txt", "../../../data/secret"),
        )
        assert response.status_code == 422
        assert "符号链接" in response.text

    def test_rejected_deploy_does_not_bump_version(
        self, admin_client, test_db
    ) -> None:
        _seed_event(test_db)
        _deploy(admin_client, "spring-2026", _zip_bytes({"index.html": b"ok"}))
        before = admin_client.get(f"{ADMIN}/spring-2026").json()["event"]["content_version"]

        _deploy(admin_client, "spring-2026", _zip_with_traversal("../x"))
        after = admin_client.get(f"{ADMIN}/spring-2026").json()["event"]["content_version"]
        assert before == after

    def test_too_many_entries_is_rejected(
        self, admin_client, test_db, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_CONTENT_ENTRIES", 2)
        response = _deploy(
            admin_client,
            "spring-2026",
            _zip_bytes({"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"}),
        )
        assert response.status_code == 422

    def test_oversized_extracted_content_is_rejected(
        self, admin_client, test_db, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_CONTENT_EXTRACTED_BYTES", 4)
        response = _deploy(
            admin_client, "spring-2026", _zip_bytes({"big.txt": b"0123456789"})
        )
        assert response.status_code == 413

    def test_oversized_archive_is_rejected(
        self, admin_client, test_db, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_CONTENT_ARCHIVE_BYTES", 10)
        response = _deploy(
            admin_client, "spring-2026", _zip_bytes({"a.txt": b"aaaaaaaaaaaaaaaaaaaa"})
        )
        assert response.status_code == 413

    def test_invalid_zip_is_rejected(self, admin_client, test_db) -> None:
        _seed_event(test_db)
        response = _deploy(admin_client, "spring-2026", b"not a zip at all")
        assert response.status_code == 422

    def test_empty_archive_is_rejected(self, admin_client, test_db) -> None:
        _seed_event(test_db)
        response = _deploy(admin_client, "spring-2026", _zip_bytes({}))
        assert response.status_code == 422

    def test_unknown_event_is_404(self, admin_client) -> None:
        assert _deploy(admin_client, "nope", _zip_bytes({"a.txt": b"a"})).status_code == 404

    def test_requires_admin(self, user_client, test_db) -> None:
        _seed_event(test_db)
        assert _deploy(user_client, "spring-2026", _zip_bytes({"a.txt": b"a"})).status_code == 403

    def test_no_leftover_staging_directories(
        self, admin_client, test_db, content_root
    ) -> None:
        """失败与成功路径都不该在活动目录旁边留下临时目录。"""
        _seed_event(test_db)
        _deploy(admin_client, "spring-2026", _zip_bytes({"a.txt": b"a"}))
        _deploy(admin_client, "spring-2026", _zip_with_traversal("../x"))

        leftovers = [
            p.name
            for p in (content_root / "content").iterdir()
            if p.name.startswith(".deploy-") or p.name.endswith(".previous")
        ]
        assert leftovers == []


class TestContentListing:
    """任务 7.4"""

    def test_admin_sees_files_and_sizes(self, admin_client, test_db) -> None:
        _seed_event(test_db)
        _deploy(
            admin_client,
            "spring-2026",
            _zip_bytes({"index.html": b"12345", "assets/a.css": b"123"}),
        )

        body = admin_client.get(f"{ADMIN}/spring-2026/content").json()
        assert body["event_id"] == "spring-2026"
        assert body["content_version"] == 1
        assert body["entry_path"] == "index.html"
        assert {f["path"]: f["size_bytes"] for f in body["files"]} == {
            "index.html": 5,
            "assets/a.css": 3,
        }

    def test_empty_content_lists_nothing(self, admin_client, test_db) -> None:
        _seed_event(test_db)
        assert admin_client.get(f"{ADMIN}/spring-2026/content").json()["files"] == []

    def test_plain_user_is_403(self, user_client, test_db) -> None:
        _seed_event(test_db)
        assert user_client.get(f"{ADMIN}/spring-2026/content").status_code == 403

    def test_anonymous_is_401(self, client, test_db) -> None:
        _seed_event(test_db)
        assert client.get(f"{ADMIN}/spring-2026/content").status_code == 401

    def test_unknown_event_is_404(self, admin_client) -> None:
        assert admin_client.get(f"{ADMIN}/nope/content").status_code == 404


class TestDataDirectoryIsPrivate:
    """任务 7.5：/data 绝不静态暴露（不变量 6）。"""

    def test_data_path_is_not_reachable(self, client, test_db, content_root) -> None:
        _seed_event(test_db)
        secret = content_root / "data" / "spring-2026" / "upload.pdf"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_bytes(b"private-bytes")

        response = client.get("/data/spring-2026/upload.pdf")
        assert response.status_code == 404
        assert b"private-bytes" not in response.content

    def test_response_does_not_leak_filesystem_paths(
        self, client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        response = client.get("/data/spring-2026/upload.pdf")
        assert str(content_root) not in response.text
        assert "Traceback" not in response.text

    def test_only_content_is_mounted(self, app) -> None:
        mounted = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/content" in mounted
        assert "/data" not in mounted


class TestCaseInsensitiveEventId:
    """活动标识的大小写对**文件系统**的影响。

    见 openspec/changes/case-insensitive-event-ids/。这一组断言的是落盘形态：目录名
    只能是规范形态。没有这条约束时，在大小写不敏感的文件系统（Windows / macOS）上
    `Autumn2026` 与 `autumn2026` 会指向**同一个目录**，两个活动互相覆盖内容；而在
    Linux 上是两个目录 —— 于是"本地正常、部署后行为不同"，且两侧都不报错。
    """

    def test_deploy_with_uppercase_lands_in_the_canonical_directory(
        self, admin_client, test_db, content_root
    ) -> None:
        # 以大写的标识创建，再以**大写的 URL** 投放 —— 两处都走归一化
        created = admin_client.post(
            ADMIN, json={"id": "Autumn2026", "title": "秋季招新"}
        )
        assert created.status_code == 201

        response = _deploy(
            admin_client, "AUTUMN2026", _zip_bytes({"index.html": b"<h1>ok</h1>"})
        )

        assert response.status_code == 201
        assert response.json()["event_id"] == "autumn2026"

        # 断言**磁盘上实际存在的名字**，而不是 `Path("AUTUMN2026").exists()`：
        # 后者在大小写不敏感的文件系统上恒为真，那样写出来的测试在 Windows 上等于没测
        # —— 第一版就是这么写的，它"通过"了却什么也没证明。
        names = sorted(entry.name for entry in global_settings.CONTENT_DIR.iterdir())
        assert names == ["autumn2026"]
        assert (
            global_settings.CONTENT_DIR / "autumn2026" / "index.html"
        ).read_bytes() == b"<h1>ok</h1>"

    def test_data_directory_is_canonical(self) -> None:
        """数据目录与内容目录必须用同一条规则，否则附件的落点会分叉。"""
        from app.services.content import ContentService

        service = ContentService(global_settings)

        assert service.event_dir("AUTUMN2026") == service.event_dir("autumn2026")
        assert service.data_dir("Autumn2026") == service.data_dir("autumn2026")
        assert service.event_dir("AUTUMN2026").name == "autumn2026"
        assert service.data_dir("AUTUMN2026").name == "autumn2026"

    def test_content_listing_accepts_any_case(
        self, admin_client, test_db, content_root
    ) -> None:
        _seed_event(test_db, event_id="autumn2026")
        _write_content(content_root, "autumn2026", "index.html", b"<h1>hi</h1>")

        response = admin_client.get(f"{ADMIN}/AUTUMN2026/content")

        assert response.status_code == 200
        assert response.json()["event_id"] == "autumn2026"
        assert [item["path"] for item in response.json()["files"]] == ["index.html"]

    def test_internal_path_resolution_is_canonical(self) -> None:
        """内部解析（入口校验、清单）走规范形态。

        注意**挂载出去的静态托管不走**这里：StaticFiles 直接挂在 `CONTENT_DIR` 上、
        按目录名精确取，因此它的行为就是文件系统的行为 —— 在 Windows/macOS 上
        `/content/AUTUMN2026/…` 照样取得到（实测如此），在 Linux 上是 404。

        这个平台差异是既有事实，本变更不试图消除它。代价是**前端必须用接口返回的
        规范标识去拼 iframe 地址**（见 design 决策 5），否则活动内容会在 Linux 上打不开。
        """
        from app.services.content import ContentService

        service = ContentService(global_settings)

        assert service.resolve_content_path(
            "AUTUMN2026", "index.html"
        ) == service.resolve_content_path("autumn2026", "index.html")
