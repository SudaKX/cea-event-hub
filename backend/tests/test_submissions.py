"""任务 8.1 - 8.9：提交接收、约束、去重、一致性、配额与清理。"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.clock import utcnow
from app.core.config import settings as global_settings
from app.core.enums import EventStatus, StorageState, SubmissionStatus
from app.core.security import hash_ip
from app.db.models import (
    Event,
    PendingRegistration,
    Submission,
    SubmissionFile,
    SubmitterQuota,
    UserSession,
)
from app.infra.storage_local import LocalDiskStorage
from app.services.janitor import Janitor

API = "/api/v1"
ADMIN = f"{API}/admin/events"
#: 提交的管理端路径与活动的不同前缀，别用 ADMIN 拼
ADMIN_SUBMISSIONS = f"{API}/admin/submissions"


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
    """`payload` 用哨兵做默认值，这样显式传 None 才真的发 null。

    `client_id` 默认给一个：匿名提交现在**必须**带它（见
    `SubmissionService.resolve_submitter`），不给就 422。想测缺它的情形，
    显式传 `client_id=None`。
    """
    body = {"name": "张三"} if payload is _DEFAULT_PAYLOAD else payload
    params.setdefault("client_id", "browser-test")
    if params.get("client_id") is None:
        params.pop("client_id")
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
    params.setdefault("client_id", "browser-test")
    if params.get("client_id") is None:
        params.pop("client_id")
    return client.post(
        f"{API}/events/{event_id}/submissions:files",
        data=data,
        files=multipart or None,
        params=params,
    )


def _post(client, event_id, payload, **kwargs):
    """裸 POST 的包装，用来带自定义头。

    匿名提交现在**必须**带 `client_id`（见 `SubmissionService.resolve_submitter`），
    所以这里统一补上，免得每个用例都写一遍。
    """
    params = dict(kwargs.pop("params", None) or {})
    params.setdefault("client_id", "browser-test")
    return client.post(
        f"{API}/events/{event_id}/submissions", json=payload, params=params, **kwargs
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

    def test_draft_event_is_404_not_403(self, client, test_db) -> None:
        """未发布的活动一律 404。

        如果这里返回 403（"活动未开放"），匿名用户就能用状态码区分
        "这个标识存在但没上线"与"这个标识不存在" —— 公开详情接口刻意用 404
        避免的正是这件事，两个端点必须一致。
        """
        _seed_event(test_db, status=EventStatus.DRAFT.value)
        response = _submit(client)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

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

    def test_anonymous_without_client_id_is_rejected(self, client, test_db) -> None:
        """匿名提交必须带客户端标识。

        少了它，同一活动下**所有**匿名提交都会塌缩成 `a:unknown` 一个提交者：
        管理端无法区分它们，按提交者做的分组与统计全部失真。与其让数据悄悄变质，
        不如让调用方立刻知道。
        """
        _seed_event(test_db)
        response = _submit(client, client_id=None)

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert "client_id" in error["fields"]

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 0

    def test_blank_client_id_is_rejected(self, client, test_db) -> None:
        """空白串与缺失同样不可接受 —— 它一样会塌缩成一个提交者。"""
        _seed_event(test_db)
        assert _submit(client, client_id="   ").status_code == 422

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
        first = _post(
            client, "spring-2026", {"name": "张三"}, headers={"Idempotency-Key": "key-1"}
        )
        second = _post(
            client, "spring-2026", {"name": "张三"}, headers={"Idempotency-Key": "key-1"}
        )

        assert second.status_code == 201
        assert second.json()["deduplicated"] is True
        assert (
            second.json()["submission"]["id"] == first.json()["submission"]["id"]
        )
        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1

    def test_retry_does_not_consume_quota(self, client, test_db) -> None:
        """幂等必须在配额之前：否则反复重试会把名额撞满。"""
        _seed_event(test_db, max_submissions=1)

        _post(client, "spring-2026", {"name": "张三"}, headers={"Idempotency-Key": "key-1"})
        retry = _post(
            client, "spring-2026", {"name": "张三"}, headers={"Idempotency-Key": "key-1"}
        )

        assert retry.status_code == 201
        assert retry.json()["deduplicated"] is True

        with test_db.session() as session:
            assert session.get(Event, "spring-2026").submission_count == 1

    def test_retry_returns_original_even_when_full(self, client, test_db) -> None:
        _seed_event(test_db, max_submissions=1)
        first = _post(client, "spring-2026", {"a": 1}, headers={"Idempotency-Key": "k"})
        assert first.status_code == 201

        # 名额已满，但重试同一幂等键应返回原提交而不是 409
        retry = _post(client, "spring-2026", {"a": 1}, headers={"Idempotency-Key": "k"})
        assert retry.status_code == 201
        assert retry.json()["submission"]["id"] == first.json()["submission"]["id"]

    def test_different_keys_create_independent_rows(self, client, test_db) -> None:
        _seed_event(test_db)
        for key, payload in (("k1", {"n": 1}), ("k2", {"n": 2})):
            _post(client, "spring-2026", payload, headers={"Idempotency-Key": key})
        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2

    def test_identical_content_without_a_key_is_not_deduplicated(
        self, client, test_db
    ) -> None:
        """**刻意不去重。** 理由见 `services/submissions.py`。

        按内容哈希去重不看请求身份、只看内容相似度。重复提交留下两条记录是响亮
        且可恢复的（管理员看得见、删掉即释放名额），而误判会把用户的提交连同
        附件一起静默丢弃。这条测试把新行为钉住，免得日后被当成缺陷"修"回去。
        """
        _seed_event(test_db)
        first = _submit(client, {"name": "张三"})
        second = _submit(client, {"name": "张三"})

        assert first.json()["deduplicated"] is False
        assert second.json()["deduplicated"] is False
        assert second.json()["submission"]["id"] != first.json()["submission"]["id"]

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2

    def test_corrected_attachment_is_not_swallowed(self, client, test_db) -> None:
        """移除内容去重的直接动因：字段没改、只换了一个附件。

        旧实现会把这个第二次提交判定为"重复"并丢弃 —— 用户看到"已收到"，
        以为成功了，而新附件已经没了。这比多一条记录严重得多。
        """
        _seed_event(test_db)
        payload = {"name": "张三"}

        first = _submit_files(
            client, payload=payload, files=[("photo-a.jpg", b"wrong photo")]
        )
        second = _submit_files(
            client, payload=payload, files=[("photo-b.jpg", b"right photo")]
        )

        assert first.json()["deduplicated"] is False
        assert second.json()["deduplicated"] is False

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2
            names = set(
                session.scalars(select(SubmissionFile.original_name)).all()
            )
        assert names == {"photo-a.jpg", "photo-b.jpg"}

    def test_idempotency_key_is_the_only_dedup(self, client, test_db) -> None:
        """同一个幂等键才叫重复；不同键、哪怕内容一模一样，也是两次提交。"""
        _seed_event(test_db)
        payload = {"name": "张三"}
        first = _post(client, "spring-2026", payload, headers={"Idempotency-Key": "intent-1"})
        replay = _post(client, "spring-2026", payload, headers={"Idempotency-Key": "intent-1"})
        different_intent = _post(
            client, "spring-2026", payload, headers={"Idempotency-Key": "intent-2"}
        )

        assert first.json()["deduplicated"] is False
        assert replay.json()["deduplicated"] is True
        assert replay.json()["submission"]["id"] == first.json()["submission"]["id"]
        assert different_intent.json()["deduplicated"] is False

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2

    def test_key_is_scoped_to_the_event(self, client, test_db) -> None:
        _seed_event(test_db, "spring-2026")
        _seed_event(test_db, "autumn-2026")

        for event_id in ("spring-2026", "autumn-2026"):
            response = _post(
                client, event_id, {"a": 1}, headers={"Idempotency-Key": "same-key"}
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


class TestSubmitterQuota:
    """单个提交者的份数上限。

    计数器存在的理由是**原子性**：读一下再写在 MySQL 的可重复读下会静默超限。
    因此这一组除了功能，还要盯住"计数与实际行数一致"。
    """

    def _counts(self, test_db, event_id: str = "spring-2026") -> dict[str, int]:
        with test_db.session() as session:
            return {
                row.submitter: row.used
                for row in session.scalars(
                    select(SubmitterQuota).where(SubmitterQuota.event_id == event_id)
                )
            }

    def test_allows_up_to_the_per_submitter_limit(self, client, test_db) -> None:
        _seed_event(test_db, max_per_submitter=2)

        assert _submit(client, {"n": 1}).status_code == 201
        assert _submit(client, {"n": 2}).status_code == 201

        response = _submit(client, {"n": 3})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "submitter_quota_exhausted"

    def test_other_submitters_are_unaffected(self, client, test_db) -> None:
        """这是它与活动满额必须分开成两个错误码的理由。

        活动满额是"整个活动没位置了"，个人满额是"你不能再交了，别人还可以" ——
        活动页据此给出的提示完全不同。
        """
        _seed_event(test_db, max_per_submitter=1)

        assert _submit(client, {"n": 1}, client_id="alice").status_code == 201
        assert _submit(client, {"n": 2}, client_id="alice").status_code == 409
        assert _submit(client, {"n": 3}, client_id="bob").status_code == 201

    def test_unlimited_event_still_maintains_the_counter(self, client, test_db) -> None:
        """不限额也要维护计数。

        否则"以后才设上限"就得再回填一次，还会出现"设上限之前交的那些不算数"。
        """
        _seed_event(test_db)  # 不设 max_per_submitter

        for index in range(3):
            assert _submit(client, {"n": index}, client_id="alice").status_code == 201

        assert self._counts(test_db) == {"a:alice": 3}

    def test_setting_a_limit_later_counts_existing_rows(self, test_db) -> None:
        """**这是回填存在的理由。**

        计数器在功能上线时按既有提交回填。不回填的话，管理员把活动设成"每人最多
        1 份"、而某人此前已交过 3 份时，计数器里没有他的行，下一次提交会走
        "行不存在 → 插入 used=1"这条路直接成功，超限静默通过。
        """
        _seed_event(test_db)
        with test_db.session() as session:
            for index in range(3):
                session.add(
                    Submission(
                        event_id="spring-2026",
                        submitter="a:alice",
                        payload={"n": index},
                    )
                )

        from app.db.models import SubmitterQuota as Quota
        from app.repositories.submissions import SubmitterQuotaRepository

        # 模拟迁移的回填：按实际行数建立计数
        with test_db.session() as session:
            session.execute(
                text(
                    "INSERT INTO submitter_quotas (event_id, submitter, used) "
                    "SELECT event_id, submitter, COUNT(*) FROM submissions "
                    "GROUP BY event_id, submitter"
                )
            )

        assert self._counts(test_db) == {"a:alice": 3}
        with test_db.session() as session:
            assert not SubmitterQuotaRepository().try_acquire(
                session, "spring-2026", "a:alice", limit=3
            )
            assert session.get(Quota, ("spring-2026", "a:alice")).used == 3

    def test_deleting_a_submission_frees_the_slot(self, admin_client, client, test_db) -> None:
        """删一条要退还个人名额 —— 否则管理员救不回那个人。"""
        _seed_event(test_db, max_per_submitter=1)

        first = _submit(client, {"n": 1}, client_id="alice")
        assert first.status_code == 201
        assert _submit(client, {"n": 2}, client_id="alice").status_code == 409

        submission_id = first.json()["submission"]["id"]
        assert admin_client.delete(f"{ADMIN_SUBMISSIONS}/{submission_id}").status_code == 204

        assert _submit(client, {"n": 3}, client_id="alice").status_code == 201

    def test_batch_delete_recomputes_the_counter(self, admin_client, client, test_db) -> None:
        _seed_event(test_db, max_per_submitter=5)

        ids = []
        for index in range(3):
            response = _submit(client, {"n": index}, client_id="alice")
            ids.append(response.json()["submission"]["id"])
        assert self._counts(test_db) == {"a:alice": 3}

        response = admin_client.post(f"{ADMIN_SUBMISSIONS}:delete", json={"ids": ids})
        assert response.status_code == 200

        assert self._counts(test_db) == {"a:alice": 0}
        # 名额确实回来了
        assert _submit(client, {"n": 9}, client_id="alice").status_code == 201

    def test_idempotent_replay_does_not_consume_a_slot(self, client, test_db) -> None:
        """幂等命中直接返回原提交，不该再占一份。

        幂等键走 `Idempotency-Key` 请求头；重放返回 **201**（不是 200），靠
        `deduplicated` 区分。
        """
        _seed_event(test_db, max_per_submitter=1)
        url = f"{API}/events/spring-2026/submissions"
        params = {"client_id": "alice"}
        headers = {"Idempotency-Key": "k1"}

        first = client.post(url, json={"n": 1}, params=params, headers=headers)
        assert first.status_code == 201

        # 同键重放：应当原样返回，而不是撞上个人上限
        replay = client.post(url, json={"n": 1}, params=params, headers=headers)
        assert replay.status_code == 201
        assert replay.json()["deduplicated"] is True
        assert replay.json()["submission"]["id"] == first.json()["submission"]["id"]

        # 计数仍是 1：那次重放没有偷偷占掉第二份，也没有把第一份退掉
        assert self._counts(test_db) == {"a:alice": 1}
        # 不带幂等键就是新的一次提交，被个人上限挡下
        assert client.post(url, json={"n": 2}, params=params).status_code == 409

    def test_failed_submission_rolls_back_the_counter(
        self, fault_client, test_db, monkeypatch
    ) -> None:
        from app.repositories.submissions import SubmissionRepository

        _seed_event(test_db, max_per_submitter=3)

        def boom(self, session, submission):
            raise RuntimeError("模拟落库失败")

        monkeypatch.setattr(SubmissionRepository, "add", boom)
        assert _submit(fault_client, {"n": 1}, client_id="alice").status_code == 500

        assert self._counts(test_db) == {}

    def test_counter_stays_in_step_with_actual_rows(self, client, test_db) -> None:
        """计数与真实行数必须一致 —— 它是从行数派生出来的，长期偏离就成了假数据。"""
        _seed_event(test_db, max_per_submitter=10)

        for index in range(4):
            assert _submit(client, {"n": index}, client_id="alice").status_code == 201

        with test_db.session() as session:
            actual = session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.submitter == "a:alice")
            )
        assert self._counts(test_db) == {"a:alice": actual}

    def test_requires_login_event_scopes_the_limit_per_user(self, user_client, test_db) -> None:
        """需登录时提交者是可信的 `u:{user_id}`，限额因此真正生效。"""
        _seed_event(test_db, submission_requires_login=True, max_per_submitter=1)

        assert _submit(user_client, {"n": 1}).status_code == 201
        assert _submit(user_client, {"n": 2}).status_code == 409
        assert self._counts(test_db) == {"u:1": 1}

    def test_zero_is_rejected_by_the_schema(self, admin_client, test_db) -> None:
        """上限 0 等于谁都交不了，那应当通过把活动下架表达，而不是一个隐晦的配额。"""
        _seed_event(test_db)
        response = admin_client.patch(
            f"{ADMIN}/spring-2026", json={"max_per_submitter": 0}
        )
        assert response.status_code == 422

    def test_admin_can_set_and_clear_the_limit(self, admin_client, test_db) -> None:
        _seed_event(test_db)

        assert admin_client.patch(
            f"{ADMIN}/spring-2026", json={"max_per_submitter": 3}
        ).json()["event"]["max_per_submitter"] == 3
        assert admin_client.patch(
            f"{ADMIN}/spring-2026", json={"max_per_submitter": None}
        ).json()["event"]["max_per_submitter"] is None

    def test_anonymous_limit_is_only_advice(self, client, test_db) -> None:
        """**写清楚这条限制的边界，免得有人以为它是硬限制。**

        匿名提交者的标识是客户端自报的 `client_id`，换一个就是一个新的提交者 ——
        限额防的是误操作，不是故意绕过。要真正限制，得让活动要求登录。
        """
        _seed_event(test_db, max_per_submitter=1)

        assert _submit(client, {"n": 1}, client_id="alice").status_code == 201
        assert _submit(client, {"n": 2}, client_id="alice").status_code == 409
        # 换一个 client_id 就绕过了 —— 这是设计上接受的，不是缺陷
        assert _submit(client, {"n": 3}, client_id="alice-in-another-browser").status_code == 201


class TestJanitor:
    """任务 8.8"""

    def _janitor(self, app, test_db, storage, *, limiter=None) -> Janitor:
        return Janitor(
            database=test_db,
            settings=app.state.settings,
            storage=storage,
            limiter=limiter if limiter is not None else app.state.rate_limiter,
        )

    def test_removes_expired_registration_pendings(
        self, app, test_db, storage, client
    ) -> None:
        """过期占位要被**真正删掉**，用户名与邮箱才能重新可用。

        唯一性由唯一索引保证，而索引不认时间：只标记不过期的做法会让那个槽位一直
        被占着。注册请求时也会清一次与之冲突的过期行（到期即刻释放），这里是
        没人注册时的兜底。
        """
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "password": "correct-horse",
                "email": "alice@example.com",
            },
        )
        with test_db.session() as session:
            pending = session.scalar(select(PendingRegistration))
            assert pending is not None
            pending.expires_at = utcnow() - timedelta(seconds=1)

        report = self._janitor(app, test_db, storage).run_once()
        assert report.pending_registrations_removed == 1

        with test_db.session() as session:
            assert session.scalar(select(PendingRegistration)) is None
        # 槽位释放了：同样的用户名与邮箱可以重新注册
        assert (
            client.post(
                "/api/v1/auth/register",
                json={
                    "username": "alice",
                    "password": "correct-horse",
                    "email": "alice@example.com",
                },
            ).status_code
            == 202
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
