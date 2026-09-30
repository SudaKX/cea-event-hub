"""任务 6.1 - 6.4：活动生命周期、公开目录、管理端维护、配额求值。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.config import settings as global_settings
from app.core.enums import EventStatus
from app.db.models import Event
from app.services.events import EventService

API = "/api/v1"
ADMIN = f"{API}/admin/events"
PUBLIC = f"{API}/events"


def _create(admin_client, event_id="spring-2026", **overrides):
    body = {"id": event_id, "title": "春季招新", **overrides}
    return admin_client.post(ADMIN, json=body)


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


class TestCreate:
    """任务 6.1 / 6.3"""

    def test_new_event_starts_as_draft(self, admin_client, test_db) -> None:
        response = _create(admin_client)
        assert response.status_code == 201
        assert response.json()["event"]["status"] == EventStatus.DRAFT.value

        # 草稿不出现在公开接口
        assert admin_client.get(f"{PUBLIC}/spring-2026").status_code == 404
        assert admin_client.get(PUBLIC).json()["events"] == []

    def test_owner_is_recorded(self, admin_client, admin_id) -> None:
        body = _create(admin_client).json()["event"]
        assert body["owner_id"] == admin_id

    def test_duplicate_id_is_rejected(self, admin_client) -> None:
        assert _create(admin_client).status_code == 201
        response = _create(admin_client)
        assert response.status_code == 409

    @pytest.mark.parametrize("bad", ["Spring", "with space", "has.dot", "a", "-lead"])
    def test_invalid_event_id_is_rejected(self, admin_client, bad: str) -> None:
        response = _create(admin_client, event_id=bad)
        assert response.status_code == 422
        assert "id" in response.json()["error"]["fields"]

    @pytest.mark.parametrize("bad", ["/etc/passwd", "../secret", "a/../b", "C:\\x"])
    def test_invalid_entry_path_is_rejected(self, admin_client, bad: str) -> None:
        response = _create(admin_client, entry_path=bad)
        assert response.status_code == 422
        assert "entry_path" in response.json()["error"]["fields"]

    def test_unknown_field_is_rejected(self, admin_client) -> None:
        # extra="forbid"：拼错字段名会被明确拒绝，而不是静默忽略
        response = admin_client.post(
            ADMIN, json={"id": "x-event", "title": "t", "titel": "typo"}
        )
        assert response.status_code == 422


class TestEventIdImmutability:
    def test_attempt_to_change_id_is_rejected(self, admin_client) -> None:
        _create(admin_client)
        response = admin_client.patch(f"{ADMIN}/spring-2026", json={"id": "other-id"})
        assert response.status_code == 422

    def test_event_still_exists_under_original_id(self, admin_client) -> None:
        _create(admin_client)
        admin_client.patch(f"{ADMIN}/spring-2026", json={"id": "other-id"})

        assert admin_client.get(f"{ADMIN}/spring-2026").status_code == 200
        assert admin_client.get(f"{ADMIN}/other-id").status_code == 404


class TestLifecycle:
    def test_publish_makes_it_public(self, admin_client) -> None:
        _create(admin_client)
        response = admin_client.patch(
            f"{ADMIN}/spring-2026", json={"status": EventStatus.LIVE.value}
        )
        assert response.status_code == 200

        assert admin_client.get(f"{PUBLIC}/spring-2026").status_code == 200
        assert [e["id"] for e in admin_client.get(PUBLIC).json()["events"]] == [
            "spring-2026"
        ]

    def test_archive_removes_it_from_public(self, admin_client) -> None:
        _create(admin_client, status=EventStatus.LIVE.value)
        admin_client.patch(f"{ADMIN}/spring-2026", json={"status": "archived"})

        assert admin_client.get(f"{PUBLIC}/spring-2026").status_code == 404

    def test_invalid_status_is_rejected(self, admin_client) -> None:
        _create(admin_client)
        response = admin_client.patch(f"{ADMIN}/spring-2026", json={"status": "nonsense"})
        assert response.status_code == 422

    def test_close_before_open_is_rejected(self, admin_client) -> None:
        _create(admin_client)
        now = utcnow()
        response = admin_client.patch(
            f"{ADMIN}/spring-2026",
            json={
                "submissions_open_at": (now + timedelta(days=2)).isoformat(),
                "submissions_close_at": (now + timedelta(days=1)).isoformat(),
            },
        )
        assert response.status_code == 422


class TestPublicVisibility:
    """任务 6.2"""

    def test_only_live_events_are_listed(self, admin_client, test_db) -> None:
        _seed_event(test_db, "live-one", status=EventStatus.LIVE.value)
        _seed_event(test_db, "draft-one", status=EventStatus.DRAFT.value)
        _seed_event(test_db, "archived-one", status=EventStatus.ARCHIVED.value)

        ids = [e["id"] for e in admin_client.get(PUBLIC).json()["events"]]
        assert ids == ["live-one"]

    def test_draft_detail_is_404_not_403(self, admin_client, test_db) -> None:
        """403 会泄露"这个标识存在但还没上线"。"""
        _seed_event(test_db, "draft-one", status=EventStatus.DRAFT.value)
        response = admin_client.get(f"{PUBLIC}/draft-one")
        assert response.status_code == 404

    def test_detail_exposes_submission_policy_and_quota(
        self, admin_client, test_db
    ) -> None:
        _seed_event(
            test_db,
            "live-one",
            submission_requires_login=True,
            max_submissions=10,
            submission_count=3,
            content_version=2,
        )
        body = admin_client.get(f"{PUBLIC}/live-one").json()["event"]

        assert body["content_version"] == 2
        assert body["submission_requires_login"] is True
        assert body["quota"] == {"limit": 10, "used": 3, "remaining": 7}

    def test_unlimited_event_reports_null_limit(self, test_db, client) -> None:
        # 需登录的活动默认不限额 -> limit 与 remaining 都是 null
        _seed_event(test_db, "live-one", submission_requires_login=True)
        body = client.get(f"{PUBLIC}/live-one").json()["event"]
        assert body["quota"]["limit"] is None
        assert body["quota"]["remaining"] is None

    def test_full_event_reports_zero_remaining(self, test_db, client) -> None:
        _seed_event(test_db, "live-one", max_submissions=5, submission_count=5)
        body = client.get(f"{PUBLIC}/live-one").json()["event"]
        assert body["quota"]["remaining"] == 0

    def test_remaining_never_negative(self, test_db, client) -> None:
        # 计数被手工改坏时也不该出现负数剩余
        _seed_event(test_db, "live-one", max_submissions=5, submission_count=99)
        body = client.get(f"{PUBLIC}/live-one").json()["event"]
        assert body["quota"]["remaining"] == 0

    def test_anonymous_can_read_public_events(self, test_db, client) -> None:
        _seed_event(test_db, "live-one")
        assert client.get(PUBLIC).status_code == 200
        assert client.get(f"{PUBLIC}/live-one").status_code == 200


class TestAdminAuthorization:
    """任务 6.3"""

    def test_anonymous_is_401(self, client) -> None:
        assert client.get(ADMIN).status_code == 401
        assert client.post(ADMIN, json={"id": "x-event", "title": "t"}).status_code == 401

    def test_plain_user_is_403(self, user_client) -> None:
        assert user_client.get(ADMIN).status_code == 403
        assert (
            user_client.post(ADMIN, json={"id": "x-event", "title": "t"}).status_code
            == 403
        )

    def test_plain_user_cannot_modify(self, user_client, test_db) -> None:
        _seed_event(test_db, "live-one")
        assert (
            user_client.patch(f"{ADMIN}/live-one", json={"title": "x"}).status_code == 403
        )
        assert user_client.delete(f"{ADMIN}/live-one").status_code == 403

    def test_admin_list_includes_unpublished(self, admin_client, test_db) -> None:
        _seed_event(test_db, "draft-one", status=EventStatus.DRAFT.value)
        _seed_event(test_db, "live-one", status=EventStatus.LIVE.value)

        ids = {e["id"] for e in admin_client.get(ADMIN).json()["events"]}
        assert ids == {"draft-one", "live-one"}

    def test_admin_list_can_filter_by_status(self, admin_client, test_db) -> None:
        _seed_event(test_db, "draft-one", status=EventStatus.DRAFT.value)
        _seed_event(test_db, "live-one", status=EventStatus.LIVE.value)

        ids = [
            e["id"]
            for e in admin_client.get(f"{ADMIN}?status=draft").json()["events"]
        ]
        assert ids == ["draft-one"]

    def test_delete_removes_the_event(self, admin_client, test_db) -> None:
        _seed_event(test_db, "live-one")
        assert admin_client.delete(f"{ADMIN}/live-one").status_code == 204
        assert admin_client.get(f"{ADMIN}/live-one").status_code == 404

    def test_missing_event_is_404(self, admin_client) -> None:
        assert admin_client.get(f"{ADMIN}/nope").status_code == 404
        assert admin_client.patch(f"{ADMIN}/nope", json={"title": "x"}).status_code == 404
        assert admin_client.delete(f"{ADMIN}/nope").status_code == 404


class TestQuotaLimitEvaluation:
    """任务 6.4：四种组合。"""

    def _limit(self, make_settings, *, requires_login: bool, override):
        from app.core.enums import EventStatus as Status

        service = EventService(
            make_settings(
                MAX_SUBMISSIONS_PER_EVENT_ANON=4096,
                MAX_SUBMISSIONS_PER_EVENT_AUTHED=None,
            )
        )
        event = Event(
            id="e",
            title="t",
            status=Status.LIVE.value,
            submission_requires_login=requires_login,
            max_submissions=override,
        )
        return service.effective_quota_limit(event)

    def test_anonymous_without_override_uses_anon_default(self, make_settings) -> None:
        assert self._limit(make_settings, requires_login=False, override=None) == 4096

    def test_authenticated_without_override_is_unlimited(self, make_settings) -> None:
        assert self._limit(make_settings, requires_login=True, override=None) is None

    def test_override_wins_for_anonymous(self, make_settings) -> None:
        assert self._limit(make_settings, requires_login=False, override=100) == 100

    def test_override_wins_for_authenticated(self, make_settings) -> None:
        assert self._limit(make_settings, requires_login=True, override=50) == 50

    def test_override_of_zero_means_nobody_may_submit(self, make_settings) -> None:
        # 0 是合法值，不能被当成"未设置"而回落到默认
        assert self._limit(make_settings, requires_login=False, override=0) == 0

    def test_seed_value_applies_only_at_creation(
        self, admin_client, test_db, monkeypatch
    ) -> None:
        """全局配置只是创建时的种子，之后运行时唯一真源是活动字段。"""
        monkeypatch.setattr(global_settings, "SUBMISSION_REQUIRES_LOGIN_SEED", True)
        body = _create(admin_client).json()["event"]
        assert body["submission_requires_login"] is True

        # 改全局种子不应影响已存在的活动
        monkeypatch.setattr(global_settings, "SUBMISSION_REQUIRES_LOGIN_SEED", False)
        assert (
            admin_client.get(f"{ADMIN}/spring-2026").json()["event"][
                "submission_requires_login"
            ]
            is True
        )


class TestSubmissionWindow:
    def _window(self, make_settings, **overrides) -> str | None:
        service = EventService(make_settings())
        defaults: dict[str, object] = {
            "id": "e",
            "title": "t",
            "status": EventStatus.LIVE.value,
        }
        defaults.update(overrides)
        return service.submission_window_error(Event(**defaults))  # type: ignore[arg-type]

    def test_live_and_open_accepts(self, make_settings) -> None:
        assert self._window(make_settings) is None

    def test_not_live_reports_event_not_live(self, make_settings) -> None:
        assert (
            self._window(make_settings, status=EventStatus.DRAFT.value)
            == "event_not_live"
        )

    def test_before_open_reports_not_yet_open(self, make_settings) -> None:
        assert (
            self._window(make_settings, submissions_open_at=utcnow() + timedelta(days=1))
            == "not_yet_open"
        )

    def test_after_close_reports_closed_at(self, make_settings) -> None:
        assert (
            self._window(
                make_settings, submissions_close_at=utcnow() - timedelta(seconds=1)
            )
            == "closed_at"
        )
