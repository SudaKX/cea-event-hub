"""任务 8.1 - 8.9：提交接收、约束、去重、一致性、配额与清理。"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.clock import utcnow
from app.core.config import settings as global_settings
from app.core.enums import EventStatus, StorageState, SubmissionStatus
from app.core.security import hash_ip
from app.db.models import Event, Submission, SubmissionFile, UserSession
from app.infra.storage_local import LocalDiskStorage
from app.services.janitor import Janitor

API = "/api/v1"
ADMIN = f"{API}/admin/events"


@pytest.fixture
def storage(app, content_root) -> LocalDiskStorage:
    return LocalDiskStorage(content_root / "data")


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


_DEFAULT_PAYLOAD = object()


def _submit(client, payload=_DEFAULT_PAYLOAD, *, event_id="spring-2026", **params):
    """`payload` 用哨兵做默认值，这样显式传 None 才真的发 null。"""
    body = {"name": "张三"} if payload is _DEFAULT_PAYLOAD else payload
    return client.post(
        f"{API}/events/{event_id}/submissions",
        json=body,
        params=params,
    )


def _submit_files(
    client,
    *,
    event_id="spring-2026",
    payload=None,
    files=(),
    **params,
):
    data = {}
    if payload is not None:
        import json

        data["payload"] = json.dumps(payload)
    multipart = [
        ("files", (name, io.BytesIO(content), "application/octet-stream"))
        for name, content in files
    ]
    return client.post(
        f"{API}/events/{event_id}/submissions:files",
        data=data,
        files=multipart or None,
        params=params,
    )


class TestRequestShapes:
    """任务 8.1"""

    def test_json_body_is_the_payload(self, client, test_db) -> None:
        _seed_event(test_db)
        response = _submit(client, {"name": "张三", "grade": "1"})

        assert response.status_code == 201
        body = response.json()
        assert body["deduplicated"] is False
        assert body["submission"]["payload"] == {"name": "张三", "grade": "1"}
        assert body["submission"]["kind"] == "_default"
        assert body["submission"]["status"] == SubmissionStatus.RECEIVED.value

    def test_form_encoded_body_is_415(self, client, test_db) -> None:
        """强制 application/json 同时是一条免费的 CSRF 防线。"""
        _seed_event(test_db)
        response = client.post(
            f"{API}/events/spring-2026/submissions",
            data={"name": "张三"},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"

    def test_fields_and_files_produce_one_submission(
        self, client, test_db, storage
    ) -> None:
        """文件端点是超集：同一次提交只该产生一行父行。"""
        _seed_event(test_db)
        response = _submit_files(
            client,
            payload={"name": "张三"},
            files=[("a.txt", b"aaa"), ("b.txt", b"bbb")],
        )

        assert response.status_code == 201
        submission = response.json()["submission"]
        assert submission["payload"] == {"name": "张三"}
        assert len(submission["files"]) == 2

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1
            assert session.scalar(select(func.count()).select_from(SubmissionFile)) == 2

    def test_files_only_submission(self, client, test_db) -> None:
        _seed_event(test_db)
        response = _submit_files(client, files=[("only.txt", b"x")])

        assert response.status_code == 201
        assert response.json()["submission"]["payload"] == {}
        assert len(response.json()["submission"]["files"]) == 1

    def test_unknown_event_is_404(self, client) -> None:
        assert _submit(client, event_id="nope").status_code == 404

    def test_draft_event_is_closed(self, client, test_db) -> None:
        _seed_event(test_db, status=EventStatus.DRAFT.value)
        response = _submit(client)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "event_closed"

    def test_window_not_yet_open(self, client, test_db) -> None:
        _seed_event(test_db, submissions_open_at=utcnow() + timedelta(days=1))
        assert _submit(client).status_code == 403

    def test_window_closed(self, client, test_db) -> None:
        _seed_event(test_db, submissions_close_at=utcnow() - timedelta(seconds=1))
        assert _submit(client).status_code == 403

    def test_login_required_event_rejects_anonymous(self, client, test_db) -> None:
        _seed_event(test_db, submission_requires_login=True)
        response = _submit(client)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "login_required"

    def test_login_required_event_accepts_logged_in(self, user_client, test_db) -> None:
        _seed_event(test_db, submission_requires_login=True)
        assert _submit(user_client).status_code == 201


class TestPayloadConstraints:
    """任务 8.2"""

    @pytest.mark.parametrize("payload", [[1, 2, 3], "text", 42])
    def test_non_object_payload_is_rejected(self, client, test_db, payload) -> None:
        _seed_event(test_db)
        response = _submit(client, payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 0

    def test_bodyless_post_is_415(self, client, test_db) -> None:
        """没有请求体时连 Content-Type 都没有，属于"不支持的类型"而非"内容有误"。"""
        _seed_event(test_db)
        response = client.post(f"{API}/events/spring-2026/submissions")
        assert response.status_code == 415

    def test_oversized_payload_is_rejected(
        self, client, test_db, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_PAYLOAD_BYTES", 32)
        response = _submit(client, {"data": "x" * 200})
        assert response.status_code == 413

    def test_oversized_file_is_rejected_and_nothing_is_stored(
        self, client, test_db, storage, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_UPLOAD_BYTES", 4)

        response = _submit_files(client, files=[("big.bin", b"0123456789")])
        assert response.status_code == 413

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 0
        assert storage.total_bytes("spring-2026") == 0

    def test_too_many_files_is_rejected(self, client, test_db, monkeypatch) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_FILES_PER_REQUEST", 1)

        response = _submit_files(client, files=[("a.txt", b"a"), ("b.txt", b"b")])
        assert response.status_code == 422
        assert "files" in response.json()["error"]["fields"]

    def test_rejected_request_leaves_no_files(
        self, client, test_db, storage, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "MAX_UPLOAD_BYTES", 2)

        _submit_files(client, files=[("ok.txt", b"a"), ("big.bin", b"0123456789")])
        # 第一个文件已写盘，但整次提交失败 -> 必须被清掉
        assert storage.total_bytes("spring-2026") == 0


class TestKindSanitization:
    """任务 8.3"""

    def test_valid_kind_is_kept(self, client, test_db) -> None:
        _seed_event(test_db)
        body = _submit(client, kind="signup-2026").json()["submission"]
        assert body["kind"] == "signup-2026"

    @pytest.mark.parametrize("bad", ["Signup", "with space", "a/b", "..", "a.b", "a" * 65])
    def test_invalid_kind_falls_back(self, client, test_db, bad: str) -> None:
        _seed_event(test_db)
        body = _submit(client, kind=bad).json()["submission"]
        assert body["kind"] == "_default"

    def test_missing_kind_falls_back(self, client, test_db) -> None:
        _seed_event(test_db)
        assert _submit(client).json()["submission"]["kind"] == "_default"

    def test_malicious_kind_cannot_escape_the_event_directory(
        self, client, test_db, storage, content_root
    ) -> None:
        _seed_event(test_db)
        _submit_files(
            client,
            kind="../../escape",
            files=[("a.txt", b"payload")],
        )

        # 文件必须落在活动目录内，且路径里不出现跳转片段
        event_dir = content_root / "data" / "spring-2026"
        stored = [p for p in event_dir.rglob("*") if p.is_file()]
        assert stored, "文件没有被写入"
        for path in stored:
            assert event_dir in path.parents
            assert "_default" in path.parts

        assert not (content_root / "data" / "escape").exists()


class TestSubmitterIdentity:
    """任务 8.4"""

    def test_logged_in_uses_user_id(self, user_client, test_db) -> None:
        _seed_event(test_db)
        body = _submit(user_client).json()["submission"]

        with test_db.session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
        assert body["submitter"] == f"u:{user.id}"
        assert body["from_authenticated_user"] is True

    def test_client_cannot_impersonate_a_user(self, user_client, test_db) -> None:
        """登录时提交者由服务端会话推导，客户端传的值无法覆盖。"""
        _seed_event(test_db)
        body = _submit(user_client, client_id="u:9999").json()["submission"]

        with test_db.session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
        assert body["submitter"] == f"u:{user.id}"
        assert "9999" not in body["submitter"]

    def test_anonymous_with_client_id(self, client, test_db) -> None:
        _seed_event(test_db)
        body = _submit(client, client_id="browser-abc").json()["submission"]
        assert body["submitter"] == "a:browser-abc"
        assert body["from_authenticated_user"] is False

    def test_anonymous_without_client_id(self, client, test_db) -> None:
        _seed_event(test_db)
        assert _submit(client).json()["submission"]["submitter"] == "a:unknown"

    def test_ip_is_stored_salted(self, client, test_db) -> None:
        _seed_event(test_db)
        _submit(client)

        with test_db.session() as session:
            submission = session.scalar(select(Submission))
        assert submission is not None
        assert submission.ip_hash is not None
        # 加盐摘要：与直接用配置里的盐算出的值一致，且不等于裸地址
        assert submission.ip_hash == hash_ip("testclient", global_settings.IP_HASH_SALT)
        assert "testclient" not in submission.ip_hash

    def test_anonymous_client_id_is_not_a_credential(self, client, test_db) -> None:
        """匿名标识只能分组，不能认人——否则猜到一个 id 就能读别人的提交。"""
        _seed_event(test_db)
        _submit(client, client_id="browser-abc")

        response = client.get(f"{API}/me/submissions")
        assert response.status_code == 401


class TestIdempotency:
    """任务 8.5"""

    def test_same_key_returns_original(self, client, test_db) -> None:
        _seed_event(test_db)
        first = client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "张三"},
            headers={"Idempotency-Key": "key-1"},
        )
        second = client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "张三"},
            headers={"Idempotency-Key": "key-1"},
        )

        assert second.status_code == 201
        assert second.json()["deduplicated"] is True
        assert (
            second.json()["submission"]["id"] == first.json()["submission"]["id"]
        )
        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1

    def test_retry_does_not_consume_quota(self, client, test_db) -> None:
        """去重必须在配额之前：否则反复重试会把名额撞满。"""
        _seed_event(test_db, max_submissions=1)

        client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "张三"},
            headers={"Idempotency-Key": "key-1"},
        )
        retry = client.post(
            f"{API}/events/spring-2026/submissions",
            json={"name": "张三"},
            headers={"Idempotency-Key": "key-1"},
        )

        assert retry.status_code == 201
        assert retry.json()["deduplicated"] is True

        with test_db.session() as session:
            assert session.get(Event, "spring-2026").submission_count == 1

    def test_retry_returns_original_even_when_full(self, client, test_db) -> None:
        _seed_event(test_db, max_submissions=1)
        first = client.post(
            f"{API}/events/spring-2026/submissions",
            json={"a": 1},
            headers={"Idempotency-Key": "k"},
        )
        assert first.status_code == 201

        # 名额已满，但重试同一幂等键应返回原提交而不是 409
        retry = client.post(
            f"{API}/events/spring-2026/submissions",
            json={"a": 1},
            headers={"Idempotency-Key": "k"},
        )
        assert retry.status_code == 201
        assert retry.json()["submission"]["id"] == first.json()["submission"]["id"]

    def test_different_keys_create_independent_rows(self, client, test_db) -> None:
        _seed_event(test_db)
        # 内容也要不同，否则会被"窗口内相同内容"那条规则拦下
        for key, payload in (("k1", {"n": 1}), ("k2", {"n": 2})):
            client.post(
                f"{API}/events/spring-2026/submissions",
                json=payload,
                headers={"Idempotency-Key": key},
            )
        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2

    def test_content_dedup_without_a_key(self, client, test_db) -> None:
        """用户连点两下的兜底，不依赖客户端配合。"""
        _seed_event(test_db)
        first = _submit(client, {"name": "张三"})
        second = _submit(client, {"name": "张三"})

        assert second.json()["deduplicated"] is True
        assert second.json()["submission"]["id"] == first.json()["submission"]["id"]

    def test_content_dedup_is_scoped_to_the_submitter(self, client, test_db) -> None:
        _seed_event(test_db)
        _submit(client, {"name": "张三"}, client_id="browser-a")
        second = _submit(client, {"name": "张三"}, client_id="browser-b")

        assert second.json()["deduplicated"] is False

    def test_different_content_is_not_deduplicated(self, client, test_db) -> None:
        _seed_event(test_db)
        _submit(client, {"name": "张三"})
        assert _submit(client, {"name": "李四"}).json()["deduplicated"] is False

    def test_content_dedup_expires(self, client, test_db, monkeypatch) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "DEDUP_WINDOW_SECONDS", 0)
        _submit(client, {"name": "张三"})
        assert _submit(client, {"name": "张三"}).json()["deduplicated"] is False

    def test_key_is_scoped_to_the_event(self, client, test_db) -> None:
        _seed_event(test_db, "spring-2026")
        _seed_event(test_db, "autumn-2026")

        for event_id in ("spring-2026", "autumn-2026"):
            response = client.post(
                f"{API}/events/{event_id}/submissions",
                json={"a": 1},
                headers={"Idempotency-Key": "same-key"},
            )
            assert response.json()["deduplicated"] is False

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2


class TestStorageConsistency:
    """任务 8.6"""

    def test_original_filename_never_shapes_the_path(
        self, client, test_db, storage, content_root
    ) -> None:
        _seed_event(test_db)
        response = _submit_files(
            client, files=[("../../etc/passwd", b"malicious")]
        )
        assert response.status_code == 201

        # 原始名只作为元信息保留
        assert (
            response.json()["submission"]["files"][0]["original_name"]
            == "../../etc/passwd"
        )

        event_dir = content_root / "data" / "spring-2026"
        for path in event_dir.rglob("*"):
            if path.is_file():
                assert event_dir in path.parents
                assert "passwd" not in path.name

        assert not (content_root / "data" / "etc").exists()

    def test_files_are_committed_after_success(self, client, test_db) -> None:
        _seed_event(test_db)
        _submit_files(client, files=[("a.txt", b"aaa")])

        with test_db.session() as session:
            record = session.scalar(select(SubmissionFile))
        assert record is not None
        assert record.storage_state == StorageState.COMMITTED.value

    def test_database_failure_leaves_no_orphan_bytes(
        self, fault_client, test_db, storage, monkeypatch
    ) -> None:
        """数据库失败时磁盘上的字节必须被清掉。"""
        from app.repositories.submissions import SubmissionRepository

        _seed_event(test_db)

        def boom(self, session, submission):
            raise RuntimeError("模拟落库失败")

        monkeypatch.setattr(SubmissionRepository, "add", boom)

        response = _submit_files(fault_client, files=[("a.txt", b"aaa")])
        assert response.status_code == 500

        assert storage.total_bytes("spring-2026") == 0
        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 0

    def test_sha256_is_recorded(self, client, test_db) -> None:
        import hashlib

        _seed_event(test_db)
        response = _submit_files(client, files=[("a.txt", b"hello")])
        expected = hashlib.sha256(b"hello").hexdigest()
        assert response.json()["submission"]["files"][0]["sha256"] == expected


class TestQuota:
    """任务 8.7"""

    def test_accepts_up_to_the_limit(self, client, test_db) -> None:
        _seed_event(test_db, max_submissions=2)

        assert _submit(client, {"n": 1}).status_code == 201
        assert _submit(client, {"n": 2}).status_code == 201

        response = _submit(client, {"n": 3})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "quota_exhausted"

    def test_refused_submission_creates_no_row(self, client, test_db) -> None:
        _seed_event(test_db, max_submissions=1)
        _submit(client, {"n": 1})
        _submit(client, {"n": 2})

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1

    def test_quota_exhausted_is_distinct_from_rate_limit(
        self, client, test_db
    ) -> None:
        """409 是终态，429 是"稍后重试"——客户端要能区分。"""
        _seed_event(test_db, max_submissions=1)
        _submit(client, {"n": 1})
        assert _submit(client, {"n": 2}).status_code == 409

    def test_unlimited_event_keeps_counting(self, user_client, test_db) -> None:
        """不限额时仍然计数，供管理端展示已收条数。

        需登录的活动默认不限额，所以这里必须用已登录客户端——匿名请求会先被
        登录要求挡下。
        """
        _seed_event(test_db, "unlimited", submission_requires_login=True)
        for index in range(3):
            response = _submit(user_client, {"n": index}, event_id="unlimited")
            assert response.status_code == 201, response.text

        with test_db.session() as session:
            assert session.get(Event, "unlimited").submission_count == 3

    def test_failure_rolls_back_the_quota(self, fault_client, test_db, monkeypatch) -> None:
        """写入失败时名额必须回退——整体回滚即可，无需额外代码。"""
        from app.repositories.submissions import SubmissionRepository

        _seed_event(test_db, max_submissions=5)

        def boom(self, session, submission):
            raise RuntimeError("模拟落库失败")

        monkeypatch.setattr(SubmissionRepository, "add", boom)
        assert _submit(fault_client, {"n": 1}).status_code == 500

        with test_db.session() as session:
            assert session.get(Event, "spring-2026").submission_count == 0

    def test_zero_quota_blocks_everyone(self, client, test_db) -> None:
        _seed_event(test_db, max_submissions=0)
        assert _submit(client).status_code == 409


class TestJanitor:
    """任务 8.8"""

    def _janitor(self, app, test_db, storage, *, limiter=None) -> Janitor:
        return Janitor(
            database=test_db,
            settings=app.state.settings,
            storage=storage,
            limiter=limiter if limiter is not None else app.state.rate_limiter,
        )

    def test_removes_orphan_bytes(
        self, app, test_db, storage, content_root, monkeypatch
    ) -> None:
        """崩溃真正留下的是无人认领的字节，而不是 pending 记录。"""
        _seed_event(test_db)
        orphan = content_root / "data" / "spring-2026" / "_default" / "2026" / "01" / "x.bin"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")
        # 让文件"足够旧"以越过宽限期
        import os

        old = utcnow().timestamp() - 10_000
        os.utime(orphan, (old, old))

        report = self._janitor(app, test_db, storage).run_once()
        assert report.orphan_files_removed == 1
        assert not orphan.exists()

    def test_respects_the_grace_period(
        self, app, test_db, storage, content_root
    ) -> None:
        """正在写入、尚未落库的文件同样"没有记录"，不能删。"""
        _seed_event(test_db)
        in_flight = content_root / "data" / "spring-2026" / "_default" / "uploading.bin"
        in_flight.parent.mkdir(parents=True, exist_ok=True)
        in_flight.write_bytes(b"still uploading")

        report = self._janitor(app, test_db, storage).run_once()
        assert report.orphan_files_removed == 0
        assert in_flight.exists()

    def test_keeps_referenced_files(
        self, app, client, test_db, storage, content_root
    ) -> None:
        _seed_event(test_db)
        _submit_files(client, files=[("a.txt", b"keep me")])

        import os

        for path in (content_root / "data").rglob("*"):
            if path.is_file():
                old = utcnow().timestamp() - 10_000
                os.utime(path, (old, old))

        report = self._janitor(app, test_db, storage).run_once()
        assert report.orphan_files_removed == 0
        assert storage.total_bytes("spring-2026") > 0

    def test_resolves_pending_rows_whose_parent_exists(
        self, app, client, test_db, storage
    ) -> None:
        _seed_event(test_db)
        _submit_files(client, files=[("a.txt", b"aaa")])

        with test_db.session() as session:
            record = session.scalar(select(SubmissionFile))
            assert record is not None
            record.storage_state = StorageState.PENDING.value
            record.created_at = utcnow() - timedelta(days=1)

        report = self._janitor(app, test_db, storage).run_once()
        assert report.pending_files_resolved == 1

        with test_db.session() as session:
            record = session.scalar(select(SubmissionFile))
        assert record is not None
        assert record.storage_state == StorageState.COMMITTED.value

    def test_deletes_pending_rows_whose_parent_is_gone(
        self, app, test_db, storage, content_root
    ) -> None:
        """父行不存在的附件记录。

        正常路径下不可达（外键 + 级联删除会一并带走），因此这里临时关掉外键
        约束来构造该状态——这条分支是纵深防御，将来若把落库拆成两阶段就会用到。
        """
        from sqlalchemy import text

        _seed_event(test_db)
        with test_db.session() as session:
            session.execute(text("PRAGMA foreign_keys=OFF"))
            session.add(
                SubmissionFile(
                    submission_id=99999,
                    event_id="spring-2026",
                    stored_rel="_default/2026/01/ghost.bin",
                    original_name="ghost.bin",
                    size_bytes=3,
                    sha256="x",
                    storage_state=StorageState.PENDING.value,
                    created_at=utcnow() - timedelta(days=1),
                )
            )

        report = self._janitor(app, test_db, storage).run_once()
        assert report.pending_files_resolved == 1

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(SubmissionFile)) == 0

    def test_removes_expired_sessions_and_tokens(
        self, app, test_db, storage
    ) -> None:
        from app.core.enums import TokenPurpose
        from app.db.models import UserToken
        from app.repositories.users import SessionRepository

        with test_db.session() as session:
            user = User(
                username="alice",
                display_name="alice",
                password_hash="x",
                role="user",
            )
            session.add(user)
            session.flush()
            SessionRepository().add(
                session,
                UserSession(
                    token_hash="expired",
                    user_id=user.id,
                    expires_at=utcnow() - timedelta(seconds=1),
                ),
            )
            session.add(
                UserToken(
                    user_id=user.id,
                    purpose=TokenPurpose.PASSWORD_RESET.value,
                    token_hash="expired-token",
                    expires_at=utcnow() - timedelta(seconds=1),
                )
            )

        report = self._janitor(app, test_db, storage).run_once()
        assert report.sessions_removed == 1
        assert report.tokens_removed == 1

    def test_sweeps_rate_limit_keys(self, app, test_db, storage) -> None:
        """键滑出窗口后才该被清掉。

        用可注入的时间源，避免测试真的等待一个窗口。
        """
        from app.infra.ratelimit_memory import InMemoryRateLimiter

        now = {"value": 1_000.0}
        limiter = InMemoryRateLimiter(clock=lambda: now["value"])

        limiter.hit("some:key", limit=5, window_seconds=60)
        assert limiter.tracked_keys == 1

        # 时间推进到窗口之外
        now["value"] += 120
        report = self._janitor(app, test_db, storage, limiter=limiter).run_once()
        assert report.rate_limit_keys_swept == 1
        assert limiter.tracked_keys == 0

    def test_fresh_rate_limit_keys_are_kept(self, app, test_db, storage) -> None:
        """还在窗口内的键不能被清掉，否则限流会被"清理"绕过。"""
        from app.infra.ratelimit_memory import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(clock=lambda: 1_000.0)
        limiter.hit("fresh:key", limit=5, window_seconds=600)

        report = self._janitor(app, test_db, storage, limiter=limiter).run_once()
        assert report.rate_limit_keys_swept == 0
        assert limiter.tracked_keys == 1

    def test_is_idempotent(self, app, test_db, storage) -> None:
        janitor = self._janitor(app, test_db, storage)
        first = janitor.run_once()
        second = janitor.run_once()
        assert first.total >= 0 and second.total == 0


class TestMySubmissions:
    def test_returns_own_submissions(self, user_client, test_db) -> None:
        _seed_event(test_db)
        _submit(user_client, {"n": 1})
        _submit(user_client, {"n": 2})

        body = user_client.get(f"{API}/me/submissions").json()
        assert len(body["submissions"]) == 2

    def test_can_filter_by_event(self, user_client, test_db) -> None:
        _seed_event(test_db, "spring-2026")
        _seed_event(test_db, "autumn-2026")
        _submit(user_client, {"n": 1}, event_id="spring-2026")
        _submit(user_client, {"n": 2}, event_id="autumn-2026")

        body = user_client.get(
            f"{API}/me/submissions", params={"event_id": "spring-2026"}
        ).json()
        assert len(body["submissions"]) == 1

    def test_does_not_include_others_submissions(
        self, user_client, anon_client, test_db
    ) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": "anonymous"})
        _submit(user_client, {"n": "mine"})

        body = user_client.get(f"{API}/me/submissions").json()
        assert len(body["submissions"]) == 1
        assert body["submissions"][0]["payload"] == {"n": "mine"}

    def test_requires_login(self, client) -> None:
        assert client.get(f"{API}/me/submissions").status_code == 401


from app.db.models import User  # noqa: E402  （放在末尾以配合上面的夹具使用）
