"""任务 10.1 - 10.5：提交审核、删除释放配额、用户管理。"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from app.core.clock import utcnow
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


def _submit(client, payload, *, event_id="spring-2026", kind=None, files=()):
    # 匿名提交必须带 client_id（见 SubmissionService.resolve_submitter）
    params = {"client_id": "browser-review"}
    if kind:
        params["kind"] = kind
    if files:
        import json

        return client.post(
            f"{API}/events/{event_id}/submissions:files",
            data={"payload": json.dumps(payload)},
            files=[
                ("files", (name, io.BytesIO(content), "application/octet-stream"))
                for name, content in files
            ],
            params=params,
        )
    return client.post(
        f"{API}/events/{event_id}/submissions", json=payload, params=params
    )


class TestSubmissionListing:
    """任务 10.1"""

    def test_lists_submissions_of_an_event(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": 1})
        _submit(anon_client, {"n": 2})

        body = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        assert body["total"] == 2
        assert len(body["submissions"]) == 2

    def test_filter_by_kind(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": 1}, kind="signup")
        _submit(anon_client, {"n": 2}, kind="feedback")

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"kind": "signup"}
        ).json()
        assert body["total"] == 1
        assert body["submissions"][0]["kind"] == "signup"

    def test_filter_by_status(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        first = _submit(anon_client, {"n": 1}).json()["submission"]["id"]
        _submit(anon_client, {"n": 2})

        admin_client.patch(
            f"{ADMIN}/submissions/{first}",
            json={"status": SubmissionStatus.ACCEPTED.value},
        )
        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"status": SubmissionStatus.ACCEPTED.value},
        ).json()
        assert body["total"] == 1

        # 另一档也要能筛，否则"按状态筛选"只验了一半
        pending = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"status": SubmissionStatus.RECEIVED.value},
        ).json()
        assert pending["total"] == 1

        # ignored=0 是最容易被写成"缺省不过滤"的一档
        ignored = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"status": SubmissionStatus.IGNORED.value},
        ).json()
        assert ignored["total"] == 0

    def test_filter_by_submitter(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": 1}, event_id="spring-2026")
        anon_client.post(
            f"{API}/events/spring-2026/submissions",
            json={"n": 2},
            params={"client_id": "browser-x"},
        )

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"submitter": "a:browser-x"},
        ).json()
        assert body["total"] == 1

    def test_filter_by_time_range(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": 1})

        future = (utcnow() + timedelta(days=1)).isoformat()
        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"created_from": future},
        ).json()
        assert body["total"] == 0

    def test_pagination(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        for index in range(5):
            _submit(anon_client, {"n": index})

        first = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"page": 1, "page_size": 2},
        ).json()
        second = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"page": 2, "page_size": 2},
        ).json()

        assert first["total"] == 5 and len(first["submissions"]) == 2
        assert len(second["submissions"]) == 2
        assert {s["id"] for s in first["submissions"]} != {
            s["id"] for s in second["submissions"]
        }

    def test_distinguishes_authenticated_from_anonymous(
        self, admin_client, anon_client, user_client, test_db
    ) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": "anon"})
        _submit(user_client, {"n": "user"})

        body = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        flags = {s["payload"]["n"]: s["from_authenticated_user"] for s in body["submissions"]}
        assert flags == {"anon": False, "user": True}

    def test_includes_file_metadata(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": 1}, files=[("a.txt", b"hello")])

        body = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        files = body["submissions"][0]["files"]
        assert len(files) == 1
        assert files[0]["original_name"] == "a.txt"
        assert files[0]["size_bytes"] == 5

    def test_search_finds_chinese_in_the_payload(
        self, admin_client, anon_client, test_db
    ) -> None:
        """这是搜索的主要用途，也是它最容易悄悄坏掉的地方。

        JSON 列若按默认的 `json.dumps` 序列化，中文会变成 `\\uXXXX`，于是搜"张三"
        永远匹配不上（库里存的是 `\\u5f20\\u4e09`）。表现是功能"在"，但对中文完全
        无效 —— 因此这条测试用真正的中文，而不是 ASCII。
        """
        _seed_event(test_db)
        _submit(anon_client, {"name": "张三", "note": "想来"})
        _submit(anon_client, {"name": "李四", "note": "想来"})

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "张三"}
        ).json()

        assert body["total"] == 1
        assert body["submissions"][0]["payload"]["name"] == "张三"

    def test_search_matches_any_position_in_the_payload(
        self, admin_client, anon_client, test_db
    ) -> None:
        """子串匹配，不是前缀匹配：payload 没有字段级契约，搜的也不该只是某个键。"""
        _seed_event(test_db)
        _submit(anon_client, {"name": "王五", "intro": "想学嵌入式"})
        _submit(anon_client, {"name": "赵六", "intro": "想学排版"})

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "嵌入式"}
        ).json()
        assert body["total"] == 1

        # 键名本身也在被搜索的文本里
        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "intro"}
        ).json()
        assert body["total"] == 2

    def test_search_escapes_like_wildcards(self, admin_client, anon_client, test_db) -> None:
        """`%` 与 `_` 是 LIKE 的元字符，不转义的话搜它们等于搜通配。

        用户搜 `%` 是想找一个百分号，结果却拿到全部记录 —— 这种"静默地给了错误
        答案"比报错难查得多。
        """
        _seed_event(test_db)
        _submit(anon_client, {"note": "打八折 50% 优惠"})
        _submit(anon_client, {"note": "没有那个符号"})

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "50%"}
        ).json()
        assert body["total"] == 1, "「%」被当成通配符了"

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "%"}
        ).json()
        assert body["total"] == 1, "单个「%」被当成通配符了"

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "_"}
        ).json()
        assert body["total"] == 0, "「_」被当成通配符了"

    def test_search_is_scoped_to_the_event(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        _seed_event(test_db, event_id="autumn-2026")
        _submit(anon_client, {"name": "张三"})
        _submit(anon_client, {"name": "张三"}, event_id="autumn-2026")

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "张三"}
        ).json()
        assert body["total"] == 1
        assert body["submissions"][0]["event_id"] == "spring-2026"

    def test_search_combines_with_other_filters(
        self, admin_client, anon_client, test_db
    ) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"name": "张三"}, kind="signup")
        _submit(anon_client, {"name": "张三"}, kind="feedback")

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions",
            params={"q": "张三", "kind": "signup"},
        ).json()
        assert body["total"] == 1
        assert body["submissions"][0]["kind"] == "signup"

    def test_empty_search_does_not_filter(self, admin_client, anon_client, test_db) -> None:
        # 空串应当等同于"没给"，否则清空输入框会搜到一个空字符串
        _seed_event(test_db)
        _submit(anon_client, {"n": 1})
        _submit(anon_client, {"n": 2})

        assert (
            admin_client.get(
                f"{ADMIN}/events/spring-2026/submissions", params={"q": ""}
            ).json()["total"]
            == 2
        )

    def test_search_without_match_returns_empty(
        self, admin_client, anon_client, test_db
    ) -> None:
        _seed_event(test_db)
        _submit(anon_client, {"n": 1})

        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"q": "一定搜不到"}
        ).json()
        assert body["total"] == 0
        assert body["submissions"] == []

    def test_plain_user_is_403(self, user_client, test_db) -> None:
        _seed_event(test_db)
        assert user_client.get(f"{ADMIN}/events/spring-2026/submissions").status_code == 403

    def test_anonymous_is_401(self, client, test_db) -> None:
        _seed_event(test_db)
        assert client.get(f"{ADMIN}/events/spring-2026/submissions").status_code == 401


class TestReview:
    """任务 10.2"""

    def _one(self, anon_client, test_db) -> int:
        _seed_event(test_db)
        return _submit(anon_client, {"n": 1}).json()["submission"]["id"]

    def test_new_submission_starts_as_received(self, admin_client, anon_client, test_db) -> None:
        submission_id = self._one(anon_client, test_db)
        body = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        assert body["submissions"][0]["status"] == SubmissionStatus.RECEIVED.value
        assert submission_id

    @pytest.mark.parametrize(
        "target",
        [
            SubmissionStatus.IGNORED.value,
            SubmissionStatus.ACCEPTED.value,
            SubmissionStatus.RECEIVED.value,
        ],
    )
    def test_status_transitions(self, admin_client, anon_client, test_db, target: int) -> None:
        submission_id = self._one(anon_client, test_db)
        response = admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}", json={"status": target}
        )
        assert response.status_code == 200
        assert response.json()["submission"]["status"] == target

    def test_status_codes_are_the_agreed_numbers(self, admin_client, anon_client, test_db) -> None:
        """码值是对外契约（导出与筛选按它走），所以逐个钉住。

        尤其是 ignored=0：0 在布尔判断里天然表示"否"，改动它会让下游的
        `if status:` 之类写法集体反转。
        """
        assert SubmissionStatus.IGNORED.value == 0
        assert SubmissionStatus.RECEIVED.value == 1
        assert SubmissionStatus.ACCEPTED.value == 2
        # 只有三档，"审核中"已经移除
        assert len(list(SubmissionStatus)) == 3

    def test_records_actor_and_time(self, admin_client, anon_client, test_db, admin_id) -> None:
        submission_id = self._one(anon_client, test_db)
        admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}",
            json={"status": SubmissionStatus.ACCEPTED.value},
        )

        with test_db.session() as session:
            submission = session.get(Submission, submission_id)
        assert submission is not None
        assert submission.reviewed_by == admin_id
        assert submission.reviewed_at is not None

    def test_invalid_status_is_rejected(self, admin_client, anon_client, test_db) -> None:
        submission_id = self._one(anon_client, test_db)
        response = admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}", json={"status": 99}
        )
        assert response.status_code == 422
        assert "status" in response.json()["error"]["fields"]

    def test_removed_status_is_rejected(self, admin_client, anon_client, test_db) -> None:
        """旧字符串值现在必须被拒 —— 它们不再是合法码值。"""
        submission_id = self._one(anon_client, test_db)
        for stale in ("reviewing", "rejected", "accepted"):
            response = admin_client.patch(
                f"{ADMIN}/submissions/{submission_id}", json={"status": stale}
            )
            assert response.status_code == 422, stale

    def test_ignored_submission_stays_in_the_list(self, admin_client, anon_client, test_db) -> None:
        """标记为不采用**不释放名额** —— 要腾名额得删除。"""
        submission_id = self._one(anon_client, test_db)
        admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}",
            json={"status": SubmissionStatus.IGNORED.value},
        )

        body = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        assert body["total"] == 1

    def test_unknown_submission_is_404(self, admin_client) -> None:
        assert (
            admin_client.patch(
                f"{ADMIN}/submissions/9999",
                json={"status": SubmissionStatus.ACCEPTED.value},
            ).status_code
            == 404
        )

    def test_plain_user_is_403(self, user_client, anon_client, test_db) -> None:
        submission_id = self._one(anon_client, test_db)
        assert (
            user_client.patch(
                f"{ADMIN}/submissions/{submission_id}",
                json={"status": SubmissionStatus.ACCEPTED.value},
            ).status_code
            == 403
        )


class TestBatchReview:
    """批量改审核状态。

    这个端点存在的唯一理由是**省掉逐条往返**：管理端会把跨页挑选的条目攒成队列
    再统一处理，逐条 PATCH 就是几十个请求。所以下面除了行为，还专门数了 SQL 语句
    —— 如果实现退化成循环 `get`，"批量"就只剩名字了。
    """

    def _ids(self, anon_client, test_db, count: int) -> list[int]:
        _seed_event(test_db)
        return [
            _submit(anon_client, {"n": index}).json()["submission"]["id"]
            for index in range(count)
        ]

    def _statuses(self, test_db, ids: list[int]) -> dict[int, int]:
        with test_db.session() as session:
            return {
                row.id: row.status for row in session.scalars(
                    select(Submission).where(Submission.id.in_(ids))
                )
            }

    def test_reviews_every_listed_submission(self, admin_client, anon_client, test_db) -> None:
        ids = self._ids(anon_client, test_db, 4)

        response = admin_client.post(
            f"{ADMIN}/submissions:review",
            json={"ids": ids[:3], "status": SubmissionStatus.ACCEPTED.value},
        )

        assert response.status_code == 200
        assert response.json()["reviewed"] == 3

        statuses = self._statuses(test_db, ids)
        assert statuses[ids[0]] == SubmissionStatus.ACCEPTED.value
        assert statuses[ids[1]] == SubmissionStatus.ACCEPTED.value
        assert statuses[ids[2]] == SubmissionStatus.ACCEPTED.value
        # 没在列表里的那条不受影响
        assert statuses[ids[3]] == SubmissionStatus.RECEIVED.value

    def test_unknown_ids_are_skipped_not_fatal(
        self, admin_client, anon_client, test_db
    ) -> None:
        """勾选期间被别处删掉的那些跳过即可。

        为了其中一条让整批失败，管理员无从判断是哪条出了问题，只能重试 ——
        与 `:delete` 的处理一致。
        """
        ids = self._ids(anon_client, test_db, 1)

        response = admin_client.post(
            f"{ADMIN}/submissions:review",
            json={"ids": [*ids, 9999], "status": SubmissionStatus.IGNORED.value},
        )

        assert response.status_code == 200
        # 返回的是**实际改动数**，调用方据此知道结果与预期不一致
        assert response.json()["reviewed"] == 1
        assert self._statuses(test_db, ids)[ids[0]] == SubmissionStatus.IGNORED.value

    def test_records_actor_and_time(self, admin_client, anon_client, test_db, admin_id) -> None:
        ids = self._ids(anon_client, test_db, 2)

        admin_client.post(
            f"{ADMIN}/submissions:review",
            json={"ids": ids, "status": SubmissionStatus.ACCEPTED.value},
        )

        with test_db.session() as session:
            rows = session.scalars(select(Submission).where(Submission.id.in_(ids))).all()
        assert all(row.reviewed_by == admin_id for row in rows)
        assert all(row.reviewed_at is not None for row in rows)

    def test_batch_leaves_the_quota_alone(self, admin_client, anon_client, test_db) -> None:
        """改状态不释放名额 —— 要腾名额得删除。"""
        ids = self._ids(anon_client, test_db, 3)
        with test_db.session() as session:
            before = session.get(Event, "spring-2026").submission_count

        admin_client.post(
            f"{ADMIN}/submissions:review",
            json={"ids": ids, "status": SubmissionStatus.IGNORED.value},
        )

        with test_db.session() as session:
            assert session.get(Event, "spring-2026").submission_count == before

    def test_uses_one_select_for_all_of_them(
        self, admin_client, anon_client, test_db
    ) -> None:
        """取回 N 条只用一次 `IN` 查询。

        循环 `get` 的话这里会看到 N 条 SELECT —— 那样"批量"只是把 N 次请求换成了
        N 次查询，端点就白加了。
        """
        ids = self._ids(anon_client, test_db, 5)
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany) -> None:
            statements.append(statement)

        event.listen(test_db.engine, "before_cursor_execute", record)
        try:
            response = admin_client.post(
                f"{ADMIN}/submissions:review",
                json={"ids": ids, "status": SubmissionStatus.ACCEPTED.value},
            )
        finally:
            event.remove(test_db.engine, "before_cursor_execute", record)

        assert response.status_code == 200
        on_submissions = [
            sql for sql in statements if "FROM submissions" in sql or "from submissions" in sql
        ]
        assert len(on_submissions) == 1, on_submissions

    def test_invalid_status_is_rejected_once(self, admin_client, anon_client, test_db) -> None:
        ids = self._ids(anon_client, test_db, 3)

        response = admin_client.post(
            f"{ADMIN}/submissions:review", json={"ids": ids, "status": 99}
        )

        assert response.status_code == 422
        assert "status" in response.json()["error"]["fields"]
        # 一条都不该被改动
        assert set(self._statuses(test_db, ids).values()) == {SubmissionStatus.RECEIVED.value}

    def test_rejects_empty_id_list(self, admin_client) -> None:
        response = admin_client.post(
            f"{ADMIN}/submissions:review", json={"ids": [], "status": 2}
        )
        assert response.status_code == 422

    def test_rejects_oversized_id_list(self, admin_client) -> None:
        # 上限存在的意义是让一次请求的代价可预期
        response = admin_client.post(
            f"{ADMIN}/submissions:review",
            json={"ids": list(range(1, 502)), "status": 2},
        )
        assert response.status_code == 422

    def test_plain_user_is_403(self, user_client) -> None:
        response = user_client.post(
            f"{ADMIN}/submissions:review", json={"ids": [1], "status": 2}
        )
        assert response.status_code == 403


class TestDeletion:
    """任务 10.3"""

    def test_delete_releases_a_slot(self, admin_client, anon_client, test_db) -> None:
        """满额活动上"删一条腾一个名额"是管理员唯一的自救手段。"""
        _seed_event(test_db, max_submissions=2)
        first = _submit(anon_client, {"n": 1}).json()["submission"]["id"]
        _submit(anon_client, {"n": 2})

        # 已满
        assert _submit(anon_client, {"n": 3}).status_code == 409

        assert admin_client.delete(f"{ADMIN}/submissions/{first}").status_code == 204

        with test_db.session() as session:
            assert session.get(Event, "spring-2026").submission_count == 1
        # 名额恢复后可以再提交
        assert _submit(anon_client, {"n": 3}).status_code == 201

    def test_delete_removes_attachments(self, admin_client, anon_client, test_db, content_root) -> None:
        _seed_event(test_db)
        submission_id = _submit(
            anon_client, {"n": 1}, files=[("a.txt", b"hello")]
        ).json()["submission"]["id"]

        assert admin_client.delete(f"{ADMIN}/submissions/{submission_id}").status_code == 204

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(SubmissionFile)) == 0
        stored = [p for p in (content_root / "data").rglob("*") if p.is_file()]
        assert stored == [], "附件字节没有被删除"

    def test_failure_leaves_count_and_row_intact(
        self, fault_client, anon_client, test_db, monkeypatch, admin_id
    ) -> None:
        """删除失败时不能发生部分递减。"""
        from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

        _seed_event(test_db, max_submissions=5)
        submission_id = _submit(anon_client, {"n": 1}).json()["submission"]["id"]

        # fault_client 只负责"不把异常抛回测试"，身份仍需自己登录
        assert (
            fault_client.post(
                f"{API}/auth/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            ).status_code
            == 200
        )

        from app.repositories.events import EventRepository

        def boom(self, session, event_id):
            raise RuntimeError("模拟删除失败")

        monkeypatch.setattr(EventRepository, "release_submission_slot", boom)

        response = fault_client.delete(f"{ADMIN}/submissions/{submission_id}")
        assert response.status_code == 500

        monkeypatch.undo()
        with test_db.session() as session:
            assert session.get(Event, "spring-2026").submission_count == 1
            assert session.get(Submission, submission_id) is not None

    def test_unknown_submission_is_404(self, admin_client) -> None:
        assert admin_client.delete(f"{ADMIN}/submissions/9999").status_code == 404

    def test_batch_delete_keeps_count_consistent(
        self, admin_client, anon_client, test_db
    ) -> None:
        _seed_event(test_db)
        ids = [
            _submit(anon_client, {"n": index}).json()["submission"]["id"]
            for index in range(4)
        ]

        response = admin_client.post(
            f"{ADMIN}/submissions:delete", json={"ids": ids[:3]}
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 3

        with test_db.session() as session:
            event = session.get(Event, "spring-2026")
            actual = session.scalar(
                select(func.count()).select_from(Submission).where(
                    Submission.event_id == "spring-2026"
                )
            )
        assert event is not None
        assert event.submission_count == actual == 1

    def test_batch_delete_ignores_unknown_ids(self, admin_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        submission_id = _submit(anon_client, {"n": 1}).json()["submission"]["id"]

        response = admin_client.post(
            f"{ADMIN}/submissions:delete", json={"ids": [submission_id, 9999]}
        )
        assert response.json()["deleted"] == 1

    def test_batch_delete_rejects_empty_list(self, admin_client) -> None:
        assert admin_client.post(f"{ADMIN}/submissions:delete", json={"ids": []}).status_code == 422

    def test_plain_user_cannot_delete(self, user_client, anon_client, test_db) -> None:
        _seed_event(test_db)
        submission_id = _submit(anon_client, {"n": 1}).json()["submission"]["id"]
        assert user_client.delete(f"{ADMIN}/submissions/{submission_id}").status_code == 403


class TestUserBulk:
    """批量改用户角色与启用状态。

    这个类里最重要的一条是 `test_cannot_demote_all_admins_at_once` —— 它覆盖的
    正是"逐个检查最后一个管理员"会漏掉的那个漏洞。
    """

    def _seed_user(self, test_db, username="alice", **overrides) -> int:
        defaults: dict[str, object] = {
            "username": username,
            "display_name": username,
            "password_hash": hash_password("correct-horse"),
            "role": UserRole.USER.value,
        }
        defaults.update(overrides)
        with test_db.session() as session:
            user = User(**defaults)  # type: ignore[arg-type]
            session.add(user)
            session.flush()
            return user.id

    def _users(self, test_db, ids: list[int]) -> dict[int, User]:
        with test_db.session() as session:
            return {row.id: row for row in session.scalars(select(User).where(User.id.in_(ids)))}

    def test_promotes_several_users_at_once(self, admin_client, test_db) -> None:
        ids = [self._seed_user(test_db, name) for name in ("alice", "bob", "carol")]

        response = admin_client.post(f"{ADMIN}/users:bulk", json={"ids": ids, "role": "admin"})

        assert response.status_code == 200
        assert response.json()["updated"] == 3
        assert all(user.role == "admin" for user in self._users(test_db, ids).values())

    def test_disables_several_users_at_once(self, admin_client, test_db) -> None:
        ids = [self._seed_user(test_db, name) for name in ("alice", "bob")]

        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": ids, "is_active": False}
        )

        assert response.json()["updated"] == 2
        assert not any(user.is_active for user in self._users(test_db, ids).values())

    def test_role_and_active_together(self, admin_client, test_db) -> None:
        ids = [self._seed_user(test_db, "alice")]

        admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": ids, "role": "admin", "is_active": False}
        )

        user = self._users(test_db, ids)[ids[0]]
        assert (user.role, user.is_active) == ("admin", False)

    def test_cannot_demote_all_admins_at_once(self, admin_client, admin_id, test_db) -> None:
        """一次把全部管理员降级必须被拒。

        逐个调用 `set_role` 的实现会放过它：第一次检查看到"还有另一个管理员在"
        （通过），第二次检查时第一次的改动还没落库、同样通过 —— 结果一个管理员
        都不剩，系统永久失去管理能力。
        """
        other_id = self._seed_user(test_db, "bob", role=UserRole.ADMIN.value)

        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [admin_id, other_id], "role": "user"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "last_admin_protected"
        # 一条都不该被改动
        assert self._users(test_db, [admin_id, other_id])[admin_id].role == "admin"
        assert self._users(test_db, [admin_id, other_id])[other_id].role == "admin"

    def test_disabling_one_of_two_admins_is_allowed(
        self, admin_client, admin_id, test_db
    ) -> None:
        """边界要准：还剩一个可用管理员时应当允许。

        顺带说明一件事：**"停用最后一个管理员"这条路径通过接口是走不到的** ——
        想停用最后一个管理员就得把自己也列进去，而那会先撞上"不能停用自己的账号"
        （`AdminUser` 依赖本身就拒绝停用中的账号，所以操作者必然是个在岗管理员）。
        服务层那条判断因此是防御性的，见下面的服务层用例。
        """
        other_id = self._seed_user(test_db, "bob", role=UserRole.ADMIN.value)

        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [other_id], "is_active": False}
        )

        assert response.status_code == 200
        assert self._users(test_db, [other_id])[other_id].is_active is False

    def test_demoting_one_of_two_admins_is_fine(self, admin_client, admin_id, test_db) -> None:
        # 边界要准：还剩一个可用管理员时应当允许
        other_id = self._seed_user(test_db, "bob", role=UserRole.ADMIN.value)

        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [other_id], "role": "user"}
        )

        assert response.status_code == 200
        assert self._users(test_db, [other_id])[other_id].role == "user"

    def test_inactive_admin_does_not_count_as_remaining(
        self, admin_client, admin_id, test_db
    ) -> None:
        """停用的管理员不算"可用"，所以不能靠它兜底。

        先降掉唯一在岗的那个，剩下的那个虽然是 admin 但处于停用状态 —— 结果仍是
        零个可用管理员，必须被拒。
        """
        sleeping = self._seed_user(test_db, "bob", role=UserRole.ADMIN.value, is_active=False)

        # admin 自己是唯一在岗的管理员，降掉他应当被拒
        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [admin_id, sleeping], "role": "user"}
        )
        assert response.status_code == 409

    def test_cannot_disable_self_in_a_batch(self, admin_client, admin_id, test_db) -> None:
        other_id = self._seed_user(test_db, "alice")

        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [admin_id, other_id], "is_active": False}
        )

        assert response.status_code == 400
        # 整批都不生效，而不是"改了一半才发现自己在名单里"
        assert self._users(test_db, [other_id])[other_id].is_active is True

    def test_unknown_ids_are_skipped(self, admin_client, test_db) -> None:
        user_id = self._seed_user(test_db, "alice")

        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [user_id, 9999], "role": "admin"}
        )

        assert response.status_code == 200
        assert response.json()["updated"] == 1

    def test_revokes_sessions_on_any_role_change(
        self, admin_client, user_client, test_db
    ) -> None:
        """角色一变就吊销会话 —— **提升也一样**。

        单条 `set_role` 就是这么做的（对任何角色变更都吊销），批量保持一致：权限
        刚变化的那个瞬间，旧会话代表的还是旧权限，留着它等于让"降级立即生效"这句
        话有一半不成立。
        """
        with test_db.session() as session:
            alice_id = session.scalar(select(User.id).where(User.username == "alice"))
        assert alice_id is not None

        admin_client.post(f"{ADMIN}/users:bulk", json={"ids": [alice_id], "role": "admin"})

        # 提升之后旧 Cookie 已失效，得重新登录才拿到管理权限
        assert user_client.get(f"{ADMIN}/users").status_code == 401
        assert (
            user_client.post(
                "/api/v1/auth/login",
                json={"username": "alice", "password": "correct-horse"},
            ).status_code
            == 200
        )
        assert user_client.get(f"{ADMIN}/users").status_code == 200

        admin_client.post(f"{ADMIN}/users:bulk", json={"ids": [alice_id], "role": "user"})

        # 降级同样吊销：旧会话不能再访问管理接口
        assert user_client.get(f"{ADMIN}/users").status_code == 401

    def test_rejects_empty_id_list(self, admin_client) -> None:
        assert (
            admin_client.post(f"{ADMIN}/users:bulk", json={"ids": [], "role": "user"}).status_code
            == 422
        )

    def test_rejects_a_request_that_changes_nothing(self, admin_client, test_db) -> None:
        # 两个字段都不给就没有可执行的动作，与其静默成功不如拒绝
        user_id = self._seed_user(test_db, "alice")
        assert (
            admin_client.post(f"{ADMIN}/users:bulk", json={"ids": [user_id]}).status_code == 422
        )

    def test_rejects_oversized_id_list(self, admin_client) -> None:
        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": list(range(1, 202)), "role": "user"}
        )
        assert response.status_code == 422

    def test_rejects_invalid_role(self, admin_client, test_db) -> None:
        user_id = self._seed_user(test_db, "alice")
        response = admin_client.post(
            f"{ADMIN}/users:bulk", json={"ids": [user_id], "role": "superuser"}
        )
        assert response.status_code == 400

    def test_plain_user_is_403(self, user_client) -> None:
        assert (
            user_client.post(f"{ADMIN}/users:bulk", json={"ids": [1], "role": "user"}).status_code
            == 403
        )

    def test_service_refuses_to_disable_the_only_admin(self, test_db) -> None:
        """服务层单独测一次「停用最后一个管理员」。

        这条路径**通过接口走不到**：接口的操作者必然是在岗管理员（`AdminUser` 依赖
        会拒绝停用中的账号），想停用最后一个管理员就得把自己也列进去，而那会先被
        "不能停用自己的账号"挡下。

        但服务层不该依赖调用方先做过检查 —— 换个入口（脚本、以后的新接口）就会
        直接命中。所以这里绕过接口直接调用，把这条不变量钉住。
        """
        from app.core.config import settings
        from app.core.exceptions import LastAdminProtected
        from app.services.auth import AuthService

        with test_db.session() as session:
            sole_admin = User(
                username="lonely",
                display_name="lonely",
                password_hash=hash_password("correct-horse"),
                role=UserRole.ADMIN.value,
            )
            actor = User(
                username="operator",
                display_name="operator",
                password_hash=hash_password("correct-horse"),
                role=UserRole.ADMIN.value,
            )
            session.add_all([sole_admin, actor])
            session.flush()

            service = AuthService(settings)
            # 只有 sole_admin 是可用管理员（actor 还没落库完成？不，它在库里但下面
            # 先把它停用，好让 sole_admin 成为唯一一个）
            service.set_active(session, actor=actor, target_id=actor.id, is_active=True)
            session.flush()

        with test_db.session() as session:
            service = AuthService(settings)
            admin_row = session.scalar(select(User).where(User.username == "lonely"))
            actor_row = session.scalar(select(User).where(User.username == "operator"))
            assert admin_row is not None and actor_row is not None

            # 把操作者也降成普通用户，于是 lonely 是唯一的管理员
            actor_row.role = UserRole.USER.value
            session.flush()

            with pytest.raises(LastAdminProtected):
                service.bulk_update(
                    session,
                    actor=actor_row,
                    target_ids=[admin_row.id],
                    is_active=False,
                )


class TestUserManagement:
    """任务 10.4"""

    def _seed_user(self, test_db, username="alice", **overrides) -> int:
        defaults: dict[str, object] = {
            "username": username,
            "display_name": username,
            "password_hash": hash_password("correct-horse"),
            "role": UserRole.USER.value,
        }
        defaults.update(overrides)
        with test_db.session() as session:
            user = User(**defaults)  # type: ignore[arg-type]
            session.add(user)
            session.flush()
            return user.id

    def test_lists_users_without_secrets(self, admin_client, test_db) -> None:
        self._seed_user(test_db)
        body = admin_client.get(f"{ADMIN}/users").json()

        assert body["total"] >= 2  # 含管理员自己
        text = admin_client.get(f"{ADMIN}/users").text
        assert "password" not in text
        assert "argon2" not in text
        assert "token" not in text

    def test_filter_by_role(self, admin_client, test_db) -> None:
        self._seed_user(test_db, "alice", role=UserRole.USER.value)
        body = admin_client.get(f"{ADMIN}/users", params={"role": "admin"}).json()
        assert all(u["role"] == "admin" for u in body["users"])

    def test_filter_by_active(self, admin_client, test_db) -> None:
        self._seed_user(test_db, "alice", is_active=False)
        body = admin_client.get(f"{ADMIN}/users", params={"is_active": False}).json()
        assert [u["username"] for u in body["users"]] == ["alice"]

    def test_filter_by_username(self, admin_client, test_db) -> None:
        self._seed_user(test_db, "alice")
        self._seed_user(test_db, "bob")
        body = admin_client.get(f"{ADMIN}/users", params={"username": "lic"}).json()
        assert [u["username"] for u in body["users"]] == ["alice"]

    def test_promotion_takes_effect_immediately(self, admin_client, test_db) -> None:
        from fastapi.testclient import TestClient

        user_id = self._seed_user(test_db, "alice")

        with TestClient(admin_client.app) as other:
            assert _login_status(other, "alice", "correct-horse") == 200
            assert other.get(f"{ADMIN}/users").status_code == 403

            assert (
                admin_client.patch(
                    f"{ADMIN}/users/{user_id}", json={"role": "admin"}
                ).status_code
                == 200
            )

            # 提权会吊销既有会话（否则降级场景下旧会话仍持有管理权限），
            # 因此重新登录后应立刻具备管理能力
            other.cookies.clear()
            assert _login_status(other, "alice", "correct-horse") == 200
            assert other.get(f"{ADMIN}/users").status_code == 200

    def test_demotion_revokes_sessions(self, admin_client, test_db) -> None:
        from fastapi.testclient import TestClient

        user_id = self._seed_user(test_db, "alice", role=UserRole.ADMIN.value)

        with TestClient(admin_client.app) as other:
            other.post(f"{API}/auth/login", json={"username": "alice", "password": "correct-horse"})
            assert other.get(f"{ADMIN}/users").status_code == 200

            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"role": "user"})

            # 降级后旧会话立即失效
            assert other.get(f"{ADMIN}/users").status_code == 401

    def test_disable_blocks_login_and_revokes_sessions(
        self, admin_client, test_db
    ) -> None:
        from fastapi.testclient import TestClient

        user_id = self._seed_user(test_db, "alice")

        with TestClient(admin_client.app) as other:
            assert _login_status(other, "alice", "correct-horse") == 200
            assert other.get(f"{API}/auth/me").status_code == 200

            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"is_active": False})

            # 既有会话立即失效，而不是等到自然过期
            assert other.get(f"{API}/auth/me").status_code == 401

        assert _login_status(admin_client, "alice", "correct-horse") == 403

    def test_history_survives_disable(self, admin_client, user_client, test_db) -> None:
        _seed_event(test_db)
        submission_id = _submit(user_client, {"n": 1}).json()["submission"]["id"]

        with test_db.session() as session:
            user_id = session.scalar(select(User.id).where(User.username == "alice"))

        admin_client.patch(f"{ADMIN}/users/{user_id}", json={"is_active": False})

        with test_db.session() as session:
            assert session.get(Submission, submission_id) is not None

    def test_cannot_disable_self(self, admin_client, admin_id) -> None:
        response = admin_client.patch(f"{ADMIN}/users/{admin_id}", json={"is_active": False})
        assert response.status_code == 400

    def test_cannot_remove_the_last_admin(self, admin_client, admin_id, test_db) -> None:
        response = admin_client.patch(f"{ADMIN}/users/{admin_id}", json={"role": "user"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "last_admin_protected"

    def test_can_demote_when_another_admin_exists(self, admin_client, admin_id, test_db) -> None:
        other_id = self._seed_user(test_db, "bob", role=UserRole.ADMIN.value)
        assert admin_client.patch(f"{ADMIN}/users/{other_id}", json={"role": "user"}).status_code == 200

    def test_display_name_can_be_changed(self, admin_client, test_db) -> None:
        user_id = self._seed_user(test_db, "alice")
        response = admin_client.patch(
            f"{ADMIN}/users/{user_id}", json={"display_name": "爱丽丝"}
        )
        assert response.json()["user"]["display_name"] == "爱丽丝"

    def test_unknown_user_is_404(self, admin_client) -> None:
        assert admin_client.patch(f"{ADMIN}/users/9999", json={"role": "admin"}).status_code == 404
    def test_invalid_role_is_rejected(self, admin_client, test_db) -> None:
        user_id = self._seed_user(test_db, "alice")
        assert (
            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"role": "superuser"}).status_code
            == 400
        )

    def test_unknown_field_is_rejected(self, admin_client, test_db) -> None:
        user_id = self._seed_user(test_db, "alice")
        assert (
            admin_client.patch(f"{ADMIN}/users/{user_id}", json={"password_hash": "x"}).status_code
            == 422
        )

    def test_plain_user_cannot_manage_users(self, user_client) -> None:
        assert user_client.get(f"{ADMIN}/users").status_code == 403
        assert user_client.patch(f"{ADMIN}/users/1", json={"role": "admin"}).status_code == 403

    def test_anonymous_cannot_manage_users(self, client) -> None:
        assert client.get(f"{ADMIN}/users").status_code == 401


def _login_status(client, username: str, password: str) -> int:
    return client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    ).status_code
