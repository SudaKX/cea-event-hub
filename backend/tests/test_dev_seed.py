"""开发活动的启动补齐（add-develop-harness 任务 2.1 - 2.2）。

两条最要紧的断言不是"能建出来"，而是：
  - **幂等**：重复启动不产生第二个活动、也不覆盖已被改过的配置。这个活动的配置
    本来就是拿来改的（把名额设成 0 看终态、打开"需登录"看 login_required），
    每次重启都重置回去，那些改动就留不住了。
  - **非开发模式下完全惰性**：守卫写漏的后果是生产在空库启动时凭空造出一个
    **已发布**的活动，而它看起来像是有人建过的。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.config import settings as global_settings
from app.core.enums import EventStatus, EventVisibility
from app.db.models import Event
from app.db.session import Database
from app.services.dev_seed import (
    DEV_EVENT_TITLE,
    SKIP_ALREADY_EXISTS,
    SKIP_NOT_DEVELOPMENT,
    ensure_dev_event,
)

API = "/api/v1"


def _event(database: Database, event_id: str) -> Event | None:
    with database.session() as session:
        return session.get(Event, event_id)


def _count(database: Database) -> int:
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(Event)) or 0


class TestCreation:
    def test_creates_a_live_event(self, test_db, make_settings) -> None:
        outcome = ensure_dev_event(test_db, make_settings())

        assert outcome.created is True

        event = _event(test_db, global_settings.DEV_EVENT_ID)
        assert event is not None
        # 必须是已发布：未发布的活动在公开接口上是 404，草稿页连活动信息都取不到
        assert event.status == EventStatus.LIVE.value
        # 免登录、不覆盖名额上限 —— 调试台默认要能直接提交成功
        assert event.submission_requires_login is False
        assert event.max_submissions is None
        assert event.title == DEV_EVENT_TITLE

    def test_event_is_invisible_but_reachable(self, client) -> None:
        """不可见：不污染首页目录；但仍能按标识直接打开。

        这正是调试台需要的那一档 —— 每个开发者本机的首页都多出一个假活动是不能接受的。
        """
        catalog = client.get(f"{API}/events").json()["events"]
        assert global_settings.DEV_EVENT_ID not in {e["id"] for e in catalog}

        response = client.get(f"{API}/events/{global_settings.DEV_EVENT_ID}")
        assert response.status_code == 200
        assert response.json()["event"]["status"] == EventStatus.LIVE.value

    def test_title_is_recognisable_in_the_admin_list(self, admin_client) -> None:
        """它会和真实活动并排出现在管理台里，所以名字要能一眼看出用途。"""
        events = admin_client.get(f"{API}/admin/events").json()["events"]
        mine = next(
            e for e in events if e["id"] == global_settings.DEV_EVENT_ID
        )
        assert "仅本地" in mine["title"]
        assert mine["visibility"] == EventVisibility.INVISIBLE.value


class TestIdempotence:
    def test_second_run_creates_nothing(self, test_db, make_settings) -> None:
        ensure_dev_event(test_db, make_settings())
        before = _count(test_db)

        outcome = ensure_dev_event(test_db, make_settings())

        assert outcome.created is False
        assert outcome.skipped_reason == SKIP_ALREADY_EXISTS
        assert _count(test_db) == before

    def test_existing_config_is_not_overwritten(self, test_db, make_settings) -> None:
        """改过的配置必须活过重启。

        把名额上限设成 0 是"看终态"最常用的手段，打开"需登录"是另一个 ——
        每次重启把它们重置回去，等于把调试台最常用的两个动作废掉。
        """
        ensure_dev_event(test_db, make_settings())
        with test_db.session() as session:
            event = session.get(Event, global_settings.DEV_EVENT_ID)
            event.max_submissions = 0
            event.submission_requires_login = True
            event.title = "我改过的标题"

        ensure_dev_event(test_db, make_settings())

        event = _event(test_db, global_settings.DEV_EVENT_ID)
        assert event.max_submissions == 0
        assert event.submission_requires_login is True
        assert event.title == "我改过的标题"


class TestOutsideDevelopment:
    def test_creates_nothing(self, test_db, make_settings) -> None:
        # production 会要求一个非开发用的 IP_HASH_SALT（既有守卫），这里照给
        settings = make_settings(
            APP_ENV="production", IP_HASH_SALT="test-salt-not-the-dev-one"
        )

        outcome = ensure_dev_event(test_db, settings)

        assert outcome.created is False
        assert outcome.skipped_reason == SKIP_NOT_DEVELOPMENT
        assert _count(test_db) == 0

    def test_unknown_mode_is_also_inert(self, test_db, make_settings) -> None:
        """判据是"等于 development"，不是"不等于 production"。

        用后者的话，任何拼错或未预期的取值（staging、Production、空串）都会意外
        获得一个凭空出现的已发布活动。
        """
        for mode in ("staging", "Production", "", "prod"):
            ensure_dev_event(test_db, make_settings(APP_ENV=mode))

        assert _count(test_db) == 0
