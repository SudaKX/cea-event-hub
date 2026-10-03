"""任务 9.1 - 9.3：本地文件存储与附件的鉴权下载。"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.core.enums import EventStatus
from app.core.exceptions import PayloadTooLarge, StorageError
from app.db.models import Event, SubmissionFile
from app.infra.storage_local import (
    LocalDiskStorage,
    safe_extension,
    sniff_mime,
)

API = "/api/v1"


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


def _upload(client, *, event_id="spring-2026", files=(), payload=None):
    import json

    data = {"payload": json.dumps(payload)} if payload is not None else {}
    return client.post(
        f"{API}/events/{event_id}/submissions:files",
        data=data,
        files=[
            ("files", (name, io.BytesIO(content), "application/octet-stream"))
            for name, content in files
        ],
    )


class TestLocalDiskStorage:
    """任务 9.1"""

    @pytest.fixture
    def storage(self, content_root) -> LocalDiskStorage:
        return LocalDiskStorage(content_root / "data")

    def test_save_then_open(self, storage) -> None:
        result = storage.save(
            "spring-2026",
            kind="_default",
            original_name="hello.txt",
            stream=io.BytesIO(b"hello world"),
        )
        assert result.size_bytes == 11
        assert storage.exists("spring-2026", result.stored_rel)
        with storage.open("spring-2026", result.stored_rel) as handle:
            assert handle.read() == b"hello world"

    def test_delete_makes_it_unavailable(self, storage) -> None:
        result = storage.save(
            "spring-2026",
            kind="_default",
            original_name="a.txt",
            stream=io.BytesIO(b"x"),
        )
        storage.delete("spring-2026", result.stored_rel)
        assert not storage.exists("spring-2026", result.stored_rel)

    def test_delete_is_idempotent(self, storage) -> None:
        storage.delete("spring-2026", "_default/nope.txt")

    def test_path_layout_includes_kind_and_date(self, storage) -> None:
        result = storage.save(
            "spring-2026",
            kind="signup",
            original_name="a.txt",
            stream=io.BytesIO(b"x"),
        )
        parts = result.stored_rel.split("/")
        assert parts[0] == "signup"
        assert len(parts) == 4  # kind/yyyy/mm/file
        assert parts[3].endswith(".txt")

    def test_filename_is_randomized(self, storage) -> None:
        first = storage.save(
            "e", kind="_default", original_name="same.txt", stream=io.BytesIO(b"1")
        )
        second = storage.save(
            "e", kind="_default", original_name="same.txt", stream=io.BytesIO(b"2")
        )
        assert first.stored_rel != second.stored_rel

    def test_sha256_matches_content(self, storage) -> None:
        import hashlib

        result = storage.save(
            "e", kind="_default", original_name="a.bin", stream=io.BytesIO(b"abc")
        )
        assert result.sha256 == hashlib.sha256(b"abc").hexdigest()

    def test_oversized_write_leaves_nothing(self, storage) -> None:
        with pytest.raises(PayloadTooLarge):
            storage.save(
                "e",
                kind="_default",
                original_name="big.bin",
                stream=io.BytesIO(b"0123456789"),
                max_bytes=4,
            )
        assert storage.total_bytes("e") == 0

    def test_traversal_in_stored_rel_is_refused(self, storage) -> None:
        with pytest.raises(StorageError):
            storage.resolve("e", "../../../etc/passwd")

    def test_delete_refuses_traversal(self, storage, content_root) -> None:
        outside = content_root / "outside.txt"
        outside.write_text("keep me")
        storage.delete("e", "../../outside.txt")
        assert outside.exists(), "越界路径不该被删除"

    def test_total_bytes_sums_files(self, storage) -> None:
        for size in (3, 5):
            storage.save(
                "e",
                kind="_default",
                original_name="a.bin",
                stream=io.BytesIO(b"x" * size),
            )
        assert storage.total_bytes("e") == 8

    def test_remove_event_wipes_the_directory(self, storage) -> None:
        storage.save("e", kind="_default", original_name="a.bin", stream=io.BytesIO(b"x"))
        storage.remove_event("e")
        assert storage.total_bytes("e") == 0


class TestHelpers:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("a.txt", ".txt"),
            ("a.TXT", ".txt"),
            ("archive.tar.gz", ".gz"),
            ("noext", ""),
            ("evil.php", ".php"),  # 保留但无害：下载强制为附件
            ("weird.verylongextension", ""),
            ("a.", ""),
        ],
    )
    def test_safe_extension(self, name: str, expected: str) -> None:
        assert safe_extension(name) == expected

    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            (b"\x89PNG\r\n\x1a\n....", "image/png"),
            (b"\xff\xd8\xff\xe0", "image/jpeg"),
            (b"%PDF-1.7", "application/pdf"),
            (b"PK\x03\x04", "application/zip"),
            (b"hello world", "text/plain"),
            (b"\x00\x01\x02\x03", "application/octet-stream"),
        ],
    )
    def test_sniff_mime(self, head: bytes, expected: str) -> None:
        assert sniff_mime(head) == expected


class TestAttachmentDownload:
    """任务 9.2 / 9.3"""

    def _one_upload(self, client, **kwargs):
        response = _upload(client, files=[("report.pdf", b"%PDF-1.7 content")], **kwargs)
        assert response.status_code == 201, response.text
        submission = response.json()["submission"]
        return submission["id"], submission["files"][0]["id"]

    def test_owner_can_download(self, user_client, test_db) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)

        response = user_client.get(f"{API}/submissions/{submission_id}/files/{file_id}")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.7 content"

    def test_admin_can_download_anyones_attachment(
        self, user_client, admin_client, test_db
    ) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)

        response = admin_client.get(f"{API}/submissions/{submission_id}/files/{file_id}")
        assert response.status_code == 200

    def test_other_user_gets_404_not_403(
        self, user_client, anon_client, test_db, register
    ) -> None:
        """403 会确认"这个附件存在但不给你看"。"""
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)

        # 走完两阶段：停在第一步的话 bob 还不存在，后面那次请求就是匿名而非
        # "另一个普通用户"，测的就不是这条规则了
        assert register(anon_client, username="bob").status_code == 204
        assert (
            anon_client.post(
                f"{API}/auth/login",
                json={"username": "bob", "password": "correct-horse"},
            ).status_code
            == 200
        )

        response = anon_client.get(f"{API}/submissions/{submission_id}/files/{file_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_anonymous_is_401(self, user_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)
        assert (
            anon_client.get(f"{API}/submissions/{submission_id}/files/{file_id}").status_code
            == 401
        )

    def test_unknown_ids_are_404(self, user_client, test_db) -> None:
        _seed_event(test_db)
        assert user_client.get(f"{API}/submissions/9999/files/1").status_code == 404

    def test_mismatched_pair_is_404(self, user_client, test_db) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)
        # 用另一个提交的 id 去取这个附件
        assert (
            user_client.get(f"{API}/submissions/{submission_id + 100}/files/{file_id}").status_code
            == 404
        )

    def test_missing_bytes_are_404(self, user_client, test_db, content_root) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)

        for path in (content_root / "data").rglob("*"):
            if path.is_file():
                path.unlink()

        response = user_client.get(f"{API}/submissions/{submission_id}/files/{file_id}")
        assert response.status_code == 404
        assert str(content_root) not in response.text

    def test_forces_attachment_and_generic_type(self, user_client, test_db) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)

        headers = user_client.get(
            f"{API}/submissions/{submission_id}/files/{file_id}"
        ).headers
        assert headers["content-disposition"].startswith("attachment;")
        assert headers["content-type"] == "application/octet-stream"
        assert headers["x-content-type-options"] == "nosniff"

    def test_uploaded_html_is_not_rendered_inline(
        self, user_client, test_db
    ) -> None:
        """这是"接受任意文件类型"能够成立的前提。"""
        _seed_event(test_db)
        response = _upload(
            user_client,
            files=[("evil.html", b"<script>alert(1)</script>")],
        )
        submission = response.json()["submission"]
        headers = user_client.get(
            f"{API}/submissions/{submission['id']}/files/{submission['files'][0]['id']}"
        ).headers

        assert headers["content-type"] == "application/octet-stream"
        assert "attachment" in headers["content-disposition"]
        assert "text/html" not in headers["content-type"]

    def test_declared_content_type_is_not_echoed(self, user_client, test_db) -> None:
        _seed_event(test_db)
        response = user_client.post(
            f"{API}/events/spring-2026/submissions:files",
            files=[("files", ("page.html", io.BytesIO(b"<h1>x</h1>"), "text/html"))],
        )
        submission = response.json()["submission"]

        headers = user_client.get(
            f"{API}/submissions/{submission['id']}/files/{submission['files'][0]['id']}"
        ).headers
        assert headers["content-type"] == "application/octet-stream"

    def test_original_filename_is_preserved(self, user_client, test_db) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)
        disposition = user_client.get(
            f"{API}/submissions/{submission_id}/files/{file_id}"
        ).headers["content-disposition"]
        assert "report.pdf" in disposition

    def test_non_ascii_filename_uses_rfc5987(self, user_client, test_db) -> None:
        _seed_event(test_db)
        response = _upload(user_client, files=[("报名表.pdf", b"%PDF-1.7 x")])
        submission = response.json()["submission"]

        disposition = user_client.get(
            f"{API}/submissions/{submission['id']}/files/{submission['files'][0]['id']}"
        ).headers["content-disposition"]
        assert "filename*=UTF-8''" in disposition

    def test_header_injection_is_neutralized(self, user_client, test_db) -> None:
        """文件名里的引号与换行能改写响应头，必须被替换掉。"""
        _seed_event(test_db)
        response = _upload(
            user_client,
            files=[('bad"name\r\nX-Injected: 1.txt', b"x")],
        )
        submission = response.json()["submission"]

        headers = user_client.get(
            f"{API}/submissions/{submission['id']}/files/{submission['files'][0]['id']}"
        ).headers
        assert "x-injected" not in {k.lower() for k in headers}
        assert '"' not in headers["content-disposition"].split("filename*=")[0].replace(
            'filename="', ""
        ).rstrip('"; ')

    def test_downloads_are_not_cached(self, user_client, test_db) -> None:
        _seed_event(test_db)
        submission_id, file_id = self._one_upload(user_client)
        headers = user_client.get(
            f"{API}/submissions/{submission_id}/files/{file_id}"
        ).headers
        assert "no-store" in headers["cache-control"]

    def test_stored_bytes_are_not_reachable_statically(
        self, user_client, test_db, content_root
    ) -> None:
        """唯一的读取路径是上面这个端点。"""
        _seed_event(test_db)
        self._one_upload(user_client)

        stored = [p for p in (content_root / "data").rglob("*") if p.is_file()]
        assert stored
        relative = stored[0].relative_to(content_root / "data").as_posix()

        response = user_client.get(f"/data/{relative}")
        assert response.status_code == 404
