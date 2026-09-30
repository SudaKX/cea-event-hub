"""任务 10.1 - 10.5：提交审核、删除释放配额、用户管理。"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select

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
    params = {"kind": kind} if kind else {}
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

        admin_client.patch(f"{ADMIN}/submissions/{first}", json={"status": "accepted"})
        body = admin_client.get(
            f"{ADMIN}/events/spring-2026/submissions", params={"status": "accepted"}
        ).json()
        assert body["total"] == 1

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
            SubmissionStatus.REVIEWING.value,
            SubmissionStatus.ACCEPTED.value,
            SubmissionStatus.REJECTED.value,
        ],
    )
    def test_status_transitions(self, admin_client, anon_client, test_db, target: str) -> None:
        submission_id = self._one(anon_client, test_db)
        response = admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}", json={"status": target}
        )
        assert response.status_code == 200
        assert response.json()["submission"]["status"] == target

    def test_records_actor_and_time(self, admin_client, anon_client, test_db, admin_id) -> None:
        submission_id = self._one(anon_client, test_db)
        admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}", json={"status": "accepted"}
        )

        with test_db.session() as session:
            submission = session.get(Submission, submission_id)
        assert submission is not None
        assert submission.reviewed_by == admin_id
        assert submission.reviewed_at is not None

    def test_invalid_status_is_rejected(self, admin_client, anon_client, test_db) -> None:
        submission_id = self._one(anon_client, test_db)
        response = admin_client.patch(
            f"{ADMIN}/submissions/{submission_id}", json={"status": "nonsense"}
        )
        assert response.status_code == 422
        assert "status" in response.json()["error"]["fields"]

    def test_rejected_submission_stays_in_the_list(self, admin_client, anon_client, test_db) -> None:
        submission_id = self._one(anon_client, test_db)
        admin_client.patch(f"{ADMIN}/submissions/{submission_id}", json={"status": "rejected"})

        body = admin_client.get(f"{ADMIN}/events/spring-2026/submissions").json()
        assert body["total"] == 1

    def test_unknown_submission_is_404(self, admin_client) -> None:
        assert (
            admin_client.patch(f"{ADMIN}/submissions/9999", json={"status": "accepted"}).status_code
            == 404
        )

    def test_plain_user_is_403(self, user_client, anon_client, test_db) -> None:
        submission_id = self._one(anon_client, test_db)
        assert (
            user_client.patch(
                f"{ADMIN}/submissions/{submission_id}", json={"status": "accepted"}
            ).status_code
            == 403
        )


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
