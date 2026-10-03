"""任务 15.4 - 15.7：端到端验收。

这些用例不针对单个函数，而是把整条链路串起来跑一遍：内容托管 → 提交 →
附件下载 → 配额 → 管理员清理。单模块测试全绿但链路是断的情况，只有这一层能发现。
"""

from __future__ import annotations

import io
import json

from sqlalchemy import func, select

from app.core.config import settings as global_settings
from app.core.enums import EventStatus, SubmissionStatus, UserRole
from app.core.security import hash_password
from app.db.models import Event, Submission, SubmissionFile, User

API = "/api/v1"
ADMIN = f"{API}/admin"


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


def _deploy(admin_client, event_id: str, files: dict[str, bytes]):
    buffer = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return admin_client.post(
        f"{ADMIN}/events/{event_id}/content",
        files={"file": ("content.zip", buffer.getvalue(), "application/zip")},
    )


class TestCorsAsymmetry:
    """任务 15.4：两条断言必须**同时**成立。

    只满足其中一条就说明隔离机制被破坏了一半：
    - 缺了 /content 的 ACAO，活动页取不到自己的数据
    - 多了 /api 的 ACAO，活动页可以绕过宿主直接调后端
    """

    def test_content_is_reachable_from_a_sandboxed_page(
        self, client, admin_client, test_db
    ) -> None:
        _seed_event(test_db)
        assert _deploy(admin_client, "spring-2026", {"index.html": b"<h1>hi</h1>"}).status_code == 201

        response = client.get("/content/spring-2026/index.html")
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"

    def test_content_data_files_are_also_reachable(self, client, admin_client, test_db) -> None:
        """活动页取自己目录下的 JSON 也必须能成功 —— 不透明源对同主机也算跨源。"""
        _seed_event(test_db)
        _deploy(
            admin_client,
            "spring-2026",
            {"index.html": b"x", "data.json": b'{"a":1}'},
        )

        response = client.get("/content/spring-2026/data.json")
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"

    def test_api_is_unreachable_from_a_sandboxed_page(self, client) -> None:
        """封锁链：/api 一旦允许跨源，桥接代理就不再是物理边界。"""
        for path in (f"{API}/health", f"{API}/events", f"{API}/auth/me"):
            headers = {key.lower() for key in client.get(path).headers}
            assert "access-control-allow-origin" not in headers, path
            assert "access-control-allow-credentials" not in headers, path

    def test_the_two_halves_are_opposite(self, client, admin_client, test_db) -> None:
        """把不对称本身钉死：一侧有、另一侧必须没有。"""
        _seed_event(test_db)
        _deploy(admin_client, "spring-2026", {"index.html": b"x"})

        content_acao = client.get("/content/spring-2026/index.html").headers.get(
            "access-control-allow-origin"
        )
        api_acao = client.get(f"{API}/health").headers.get("access-control-allow-origin")

        assert content_acao == "*"
        assert api_acao is None


class TestSubmissionChain:
    """任务 15.5：完整提交链路。"""

    def test_full_lifecycle(self, admin_client, anon_client, user_client, test_db) -> None:
        _seed_event(test_db, max_submissions=3)
        _deploy(admin_client, "spring-2026", {"index.html": "<h1>招新</h1>".encode()})

        # --- 匿名提交 ---
        anonymous = anon_client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "匿名同学"},
            params={"client_id": "browser-a", "kind": "signup"},
        )
        assert anonymous.status_code == 201
        assert anonymous.json()["submission"]["from_authenticated_user"] is False

        # --- 登录提交 ---
        logged_in = user_client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "登录同学"},
            params={"kind": "signup"},
        )
        assert logged_in.status_code == 201
        assert logged_in.json()["submission"]["from_authenticated_user"] is True

        # --- 带附件的提交 ---
        with_files = user_client.post(
            f"{API}/events/spring-2026/submissions:files",
            data={"payload": json.dumps({"name": "带附件的同学"})},
            files=[("files", ("报名表.pdf", io.BytesIO(b"%PDF-1.7 x"), "application/pdf"))],
            params={"kind": "signup"},
        )
        assert with_files.status_code == 201
        submission = with_files.json()["submission"]
        assert len(submission["files"]) == 1

        # --- 附件下载 ---
        download = user_client.get(
            f"{API}/submissions/{submission['id']}/files/{submission['files'][0]['id']}"
        )
        assert download.status_code == 200
        assert download.content == b"%PDF-1.7 x"

        # --- 配额已满 ---
        full = anon_client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "第四位"},
            params={"client_id": "browser-b"},
        )
        assert full.status_code == 409
        assert full.json()["error"]["code"] == "quota_exhausted"

        # --- 管理员删除一条，名额恢复 ---
        assert (
            admin_client.delete(f"{ADMIN}/submissions/{anonymous.json()['submission']['id']}").status_code
            == 204
        )

        # --- 再次提交成功 ---
        retry = anon_client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "第四位"},
            params={"client_id": "browser-b"},
        )
        assert retry.status_code == 201

        # --- 计数与实际行数一致 ---
        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
            actual = session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.event_id == "spring-2026")
            )
        assert event is not None
        assert event.submission_count == actual == 3

    def test_idempotent_retry_does_not_create_a_second_row(
        self, anon_client, test_db
    ) -> None:
        _seed_event(test_db)
        key = {"Idempotency-Key": "one-key"}

        first = anon_client.post(
            f"{API}/events/spring-2026/submissions", params={"client_id": "browser-e2e"}, json={"n": 1}, headers=key
        )
        second = anon_client.post(
            f"{API}/events/spring-2026/submissions", params={"client_id": "browser-e2e"}, json={"n": 1}, headers=key
        )

        assert second.json()["deduplicated"] is True
        assert second.json()["submission"]["id"] == first.json()["submission"]["id"]

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1

    def test_login_required_event_rejects_anonymous_through_the_whole_stack(
        self, anon_client, user_client, test_db
    ) -> None:
        _seed_event(test_db, submission_requires_login=True)

        assert anon_client.post(
            f"{API}/events/spring-2026/submissions", params={"client_id": "browser-e2e"}, json={"n": 1}
        ).status_code == 401
        assert user_client.post(
            f"{API}/events/spring-2026/submissions", params={"client_id": "browser-e2e"}, json={"n": 1}
        ).status_code == 201

    def test_unpublished_event_is_not_submittable(self, anon_client, test_db) -> None:
        _seed_event(test_db, status=EventStatus.DRAFT.value)
        response = anon_client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-e2e"}, json={"n": 1})
        # draft 活动对公开接口不可见，因此是 404 而不是 403
        assert response.status_code == 404


class TestPrivacyEndToEnd:
    """任务 15.6：私有性三条断言。"""

    def _upload(self, user_client, test_db, content=b"%PDF-1.7 secret"):
        _seed_event(test_db)
        response = user_client.post(
            f"{API}/events/spring-2026/submissions:files",
            files=[("files", ("secret.pdf", io.BytesIO(content), "application/pdf"))],
        )
        submission = response.json()["submission"]
        return submission["id"], submission["files"][0]["id"]

    def test_data_directory_is_not_reachable(
        self, user_client, anon_client, test_db, content_root
    ) -> None:
        self._upload(user_client, test_db)

        stored = [p for p in (content_root / "data").rglob("*") if p.is_file()]
        assert stored, "附件字节没有落盘"
        relative = stored[0].relative_to(content_root / "data").as_posix()

        for path in (
            f"/data/{relative}",
            f"/data/spring-2026/{relative.split('/', 1)[-1]}",
        ):
            response = anon_client.get(path)
            assert response.status_code == 404, path

    def test_other_users_cannot_read_the_attachment(
        self, user_client, anon_client, test_db, register
    ) -> None:
        submission_id, file_id = self._upload(user_client, test_db)

        # 换一个普通用户（走完两阶段，否则 bob 还不存在、登录会失败）
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
        assert b"secret" not in response.content

    def test_anonymous_cannot_read_the_attachment(
        self, user_client, anon_client, test_db
    ) -> None:
        submission_id, file_id = self._upload(user_client, test_db)
        assert (
            anon_client.get(f"{API}/submissions/{submission_id}/files/{file_id}").status_code
            == 401
        )

    def test_uploaded_html_is_never_rendered_inline(
        self, user_client, test_db
    ) -> None:
        _seed_event(test_db)
        response = user_client.post(
            f"{API}/events/spring-2026/submissions:files",
            files=[
                (
                    "files",
                    ("evil.html", io.BytesIO(b"<script>alert(1)</script>"), "text/html"),
                )
            ],
        )
        submission = response.json()["submission"]
        headers = user_client.get(
            f"{API}/submissions/{submission['id']}/files/{submission['files'][0]['id']}"
        ).headers

        assert headers["content-type"] == "application/octet-stream"
        assert headers["content-disposition"].startswith("attachment;")
        assert headers["x-content-type-options"] == "nosniff"
        assert "text/html" not in headers["content-type"]

    def test_admin_can_read_any_attachment(
        self, user_client, admin_client, test_db
    ) -> None:
        submission_id, file_id = self._upload(user_client, test_db)
        response = admin_client.get(f"{API}/submissions/{submission_id}/files/{file_id}")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.7 secret"


class TestQuotaUnderConcurrency:
    """并发下配额不得被突破 —— 这是单语句 CAS 存在的理由。"""

    def test_parallel_submissions_do_not_exceed_the_cap(
        self, app, test_db, monkeypatch
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from fastapi.testclient import TestClient

        _seed_event(test_db, max_submissions=5)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 10_000)

        def one(index: int) -> int:
            # 每个线程用独立的客户端，模拟并发请求
            with TestClient(app) as client:
                return client.post(
                    f"{API}/events/spring-2026/submissions",
                    json={"n": index},
                    params={"client_id": f"browser-{index}"},
                ).status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(one, range(12)))

        accepted = statuses.count(201)
        refused = statuses.count(409)

        assert accepted == 5, f"实际接受了 {accepted} 条，超过上限"
        assert accepted + refused == 12

        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
            rows = session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.event_id == "spring-2026")
            )
        assert event is not None
        assert event.submission_count == rows == 5


class TestFullAdminFlow:
    """管理员视角的完整链路。"""

    def test_review_and_cleanup(self, admin_client, anon_client, test_db, content_root) -> None:
        _seed_event(test_db)
        ids = []
        for index in range(3):
            response = anon_client.post(
                f"{API}/events/spring-2026/submissions",
                json={"n": index},
                params={"client_id": f"b{index}"},
                files=None,
            )
            ids.append(response.json()["submission"]["id"])

        listing = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        assert listing["total"] == 3

        assert (
            admin_client.patch(
                f"{ADMIN}/submissions/{ids[0]}",
                json={"status": SubmissionStatus.ACCEPTED.value},
            ).status_code
            == 200
        )

        accepted = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"status": SubmissionStatus.ACCEPTED.value},
        ).json()
        assert accepted["total"] == 1

        batch = admin_client.post(f"{ADMIN}/submissions:delete", json={"ids": ids[1:]})
        assert batch.json()["deleted"] == 2

        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
            rows = session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.event_id == "spring-2026")
            )
        assert event is not None
        assert event.submission_count == rows == 1

    def test_user_lifecycle(self, admin_client, anon_client, test_db, register) -> None:
        # 注册（两阶段）-> 登录 -> 提权 -> 降权 -> 停用
        assert register(anon_client, username="alice").status_code == 204
        assert (
            anon_client.post(
                f"{API}/auth/login",
                json={"username": "alice", "password": "correct-horse"},
            ).status_code
            == 200
        )

        with test_db.session() as session:
            user_id = session.scalar(select(User.id).where(User.username == "alice"))

        assert (
            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"role": "admin"}).status_code
            == 200
        )
        assert (
            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"role": "user"}).status_code
            == 200
        )

        assert (
            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"is_active": False}).status_code
            == 200
        )
        assert (
            anon_client.post(
                f"{API}/auth/login",
                json={"username": "alice", "password": "correct-horse"},
            ).status_code
            == 403
        )

    def test_last_admin_protection_end_to_end(self, admin_client, admin_id) -> None:
        response = admin_client.patch(f"{ADMIN}/users/{admin_id}", json={"role": "user"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "last_admin_protected"


class TestAttachmentBytesAndRowsStayConsistent:
    """落盘与落库的一致性，走完整链路验证。"""

    def test_deleting_a_submission_removes_bytes(
        self, admin_client, user_client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        response = user_client.post(
            f"{API}/events/spring-2026/submissions:files",
            files=[("files", ("a.txt", io.BytesIO(b"hello"), "text/plain"))],
        )
        submission_id = response.json()["submission"]["id"]
        assert any(p.is_file() for p in (content_root / "data").rglob("*"))

        assert admin_client.delete(f"{ADMIN}/submissions/{submission_id}").status_code == 204

        assert [p for p in (content_root / "data").rglob("*") if p.is_file()] == []
        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(SubmissionFile)) == 0

    def test_rejected_upload_leaves_nothing_behind(
        self, admin_client, user_client, test_db, content_root, monkeypatch
    ) -> None:
        """内容包校验失败时活动内容目录完全不被触碰。"""
        _seed_event(test_db)
        assert _deploy(admin_client, "spring-2026", {"index.html": b"original"}).status_code == 201

        import stat as stat_module
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat_module.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../../../etc/passwd")

        response = admin_client.post(
            f"{ADMIN}/events/spring-2026/content",
            files={"file": ("bad.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 422

        assert (content_root / "content" / "spring-2026" / "index.html").read_bytes() == b"original"
        assert (
            admin_client.get(f"{ADMIN}/events/spring-2026").json()["event"]["content_version"]
            == 1
        )
