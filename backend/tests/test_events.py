"""任务 6.1 - 6.4：活动生命周期、公开目录、管理端维护、配额求值。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.config import settings as global_settings
from app.core.enums import EventStatus, EventVisibility
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


def _admin_event_ids(admin_client) -> list[str]:
    """管理端可见的活动标识，**剔除开发模式的调试活动**。

    开发模式下启动任务会补一个不可见、免登录的调试活动（见 services/dev_seed.py），
    于是每个测试库里都多出这一行。它与本文件要断言的东西无关，却会让"整张列表恰好
    等于某某"这类断言平白多出一项 —— 那是夹具噪声，不是行为差异。剔掉它，断言才能
    继续对**顺序与集合本身**保持精确。

    注意这里不是"改成包含即可"：本文件有好几条断言的价值就在"恰好等于"上
    （比如不可见的活动对管理端可见、且没有多出别的），放宽成超集就把它们废掉了。
    """
    return [
        event["id"]
        for event in admin_client.get(ADMIN).json()["events"]
        if event["id"] != global_settings.DEV_EVENT_ID
    ]


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


class TestEventVisibility:
    """三档可见性：0 不公开 / 1 公开 / 2 公开并置顶。

    与 `status` 正交：status 管**能不能访问**，visibility 管**在公开面露多少**。

    最容易做错的两处：把 0 做成"访问不了"（那就等于 draft，管理员没法把链接直接
    发给参与者），以及把 1 做成"哪儿都不出现"（那它就和 0 没有区别了）。
    """

    def _ids(self, admin_client) -> list[str]:
        return [e["id"] for e in admin_client.get(PUBLIC).json()["events"]]

    def test_invisible_events_are_not_listed(self, admin_client, test_db) -> None:
        _seed_event(test_db, "listed", status=EventStatus.LIVE.value)
        _seed_event(
            test_db,
            "unlisted",
            status=EventStatus.LIVE.value,
            visibility=EventVisibility.INVISIBLE.value,
        )

        assert self._ids(admin_client) == ["listed"]

    def test_invisible_event_is_still_reachable_by_id(self, client, test_db) -> None:
        """**这是这一组里最重要的一条。**

        不可见是"未公开"，不是"不存在"。做成 404 会同时伤掉两件事：管理员没法把
        链接直接发给参与者，而参与者会以为自己拿到的链接是坏的。
        """
        _seed_event(
            test_db,
            "unlisted",
            status=EventStatus.LIVE.value,
            visibility=EventVisibility.INVISIBLE.value,
        )

        response = client.get(f"{PUBLIC}/unlisted")
        assert response.status_code == 200
        assert response.json()["event"]["id"] == "unlisted"

    def test_public_events_are_listed_but_not_pinned(self, client, test_db) -> None:
        """第 1 档必须有个落脚处，否则它和"不可公开"没有区别。"""
        _seed_event(
            test_db,
            "plain",
            status=EventStatus.LIVE.value,
            visibility=EventVisibility.PUBLIC.value,
        )

        assert self._ids(client) == ["plain"]
        assert client.get(f"{PUBLIC}/plain").json()["event"]["pinned"] is False

    def test_pinned_events_sort_first(self, client, test_db) -> None:
        _seed_event(test_db, "aaa", status=EventStatus.LIVE.value)
        _seed_event(
            test_db,
            "zzz",
            status=EventStatus.LIVE.value,
            visibility=EventVisibility.PINNED.value,
        )

        # 按标识本该是 aaa 在前，置顶把它压了下去
        assert self._ids(client) == ["zzz", "aaa"]
        assert client.get(f"{PUBLIC}/zzz").json()["event"]["pinned"] is True

    def test_public_response_exposes_pinned_not_the_code(self, client, test_db) -> None:
        """公开响应给布尔而不是码值：拿到链接的访客不需要知道"这条是未公开的"。"""
        _seed_event(
            test_db,
            "unlisted",
            status=EventStatus.LIVE.value,
            visibility=EventVisibility.INVISIBLE.value,
        )
        body = client.get(f"{PUBLIC}/unlisted").json()["event"]

        assert "visibility" not in body
        assert body["pinned"] is False

    def test_existing_events_default_to_public(self, test_db) -> None:
        # 加这个字段之前所有 live 活动都在目录里，默认成别的会让它们静默消失
        _seed_event(test_db, "legacy", status=EventStatus.LIVE.value)
        with test_db.session() as session:
            assert session.get(Event, "legacy").visibility == EventVisibility.PUBLIC.value

    def test_admin_can_create_an_invisible_event(self, admin_client) -> None:
        response = _create(
            admin_client, event_id="hidden", visibility=EventVisibility.INVISIBLE.value
        )
        assert response.status_code == 201
        assert response.json()["event"]["visibility"] == EventVisibility.INVISIBLE.value

    @pytest.mark.parametrize(
        "code", [EventVisibility.INVISIBLE.value, EventVisibility.PUBLIC.value, EventVisibility.PINNED.value]
    )
    def test_admin_can_set_each_level(self, admin_client, test_db, code: int) -> None:
        _seed_event(test_db, "live-one", status=EventStatus.LIVE.value)

        body = admin_client.patch(
            f"{ADMIN}/live-one", json={"visibility": code}
        ).json()["event"]
        assert body["visibility"] == code

        # 0 不进目录，1 与 2 都进
        expected = [] if code == 0 else ["live-one"]
        assert self._ids(admin_client) == expected

    def test_admin_list_still_shows_invisible_events(self, admin_client, test_db) -> None:
        # 管理端当然要看得见，否则设成不可见之后就找不回来了
        _seed_event(
            test_db,
            "unlisted",
            status=EventStatus.LIVE.value,
            visibility=EventVisibility.INVISIBLE.value,
        )
        assert _admin_event_ids(admin_client) == ["unlisted"]

    def test_invalid_visibility_is_rejected(self, admin_client, test_db) -> None:
        _seed_event(test_db, "live-one")
        response = admin_client.patch(f"{ADMIN}/live-one", json={"visibility": 99})
        assert response.status_code == 422
        assert "visibility" in response.json()["error"]["fields"]

    def test_invalid_visibility_is_rejected_on_create(self, admin_client) -> None:
        response = _create(admin_client, event_id="bad", visibility=99)
        assert response.status_code == 422
        assert "visibility" in response.json()["error"]["fields"]

    def test_visibility_codes_are_the_agreed_numbers(self) -> None:
        """码值是对外契约（列表、首页、补全都按它走），逐个钉住。"""
        assert EventVisibility.INVISIBLE.value == 0
        assert EventVisibility.PUBLIC.value == 1
        assert EventVisibility.PINNED.value == 2
        assert len(list(EventVisibility)) == 3


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

        assert set(_admin_event_ids(admin_client)) == {"draft-one", "live-one"}

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
