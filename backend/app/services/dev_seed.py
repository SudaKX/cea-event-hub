"""开发活动的启动补齐（add-develop-harness）。

调试台要有一个**已发布**的活动才能工作：`event.info` 走的是公开接口，而未发布的
活动在那上面是 404 —— 于是草稿页连活动信息都取不到，更别提提交。

它与"首次启动引导管理员"是同一类问题（库的状态缺一样东西，补上它），因此形状对齐
`services/bootstrap.py`：以库的状态为触发条件、幂等、只在开发模式生效。

**这条路径必须在非开发模式下完全惰性。** 守卫写漏的后果不是报错，而是：生产在空库
启动时凭空造出一个**已发布**的活动 —— 那是一个可被公开访问的内容面，而且它在管理台
的活动列表里看起来像是有人建过的。因此非开发模式那一条分支要有专门的断言钉住。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.enums import EventStatus, EventVisibility
from app.db.models import Event
from app.db.session import Database
from app.repositories.events import EventRepository

logger = logging.getLogger(__name__)

SKIP_NOT_DEVELOPMENT = "not_development"
SKIP_ALREADY_EXISTS = "already_exists"

#: 开发活动的标题。**必须一眼看出用途** —— 它会和真实活动并排出现在管理台的列表里，
#: 带上"（仅本地）"是为了让误把它当成真实数据的人当场停住。
DEV_EVENT_TITLE = "开发调试活动（仅本地）"

DEV_EVENT_SUMMARY = (
    "由后端在开发模式启动时自动补齐，供 /develop 调试台缺省使用。"
    "配置可以随意改（重启不会覆盖），也可以直接删掉。"
)


@dataclass(frozen=True)
class DevSeedOutcome:
    created: bool
    event_id: str
    skipped_reason: str | None = None


def ensure_dev_event(
    database: Database,
    settings: Settings,
    *,
    target_logger: logging.Logger | None = None,
) -> DevSeedOutcome:
    """开发模式下确保存在调试用的活动；否则什么都不做。"""
    outcome = _create_if_missing(database, settings)
    log_dev_seed_outcome(outcome, target_logger)
    return outcome


def _create_if_missing(database: Database, settings: Settings) -> DevSeedOutcome:
    event_id = settings.DEV_EVENT_ID

    if not settings.is_development:
        return DevSeedOutcome(False, event_id, skipped_reason=SKIP_NOT_DEVELOPMENT)

    try:
        with database.session() as session:
            if EventRepository().get(session, event_id) is not None:
                # **不覆盖**：这个活动的配置本来就是拿来改的 —— 把名额上限设成 0
                # 看终态、打开"需登录"看 login_required。每次重启都重置回去，那些
                # 改动就留不住，而这恰恰是调试台最常用的两个动作。
                return DevSeedOutcome(
                    False, event_id, skipped_reason=SKIP_ALREADY_EXISTS
                )

            session.add(
                Event(
                    id=event_id,
                    title=DEV_EVENT_TITLE,
                    summary=DEV_EVENT_SUMMARY,
                    status=EventStatus.LIVE.value,
                    # 不可见：它不该出现在首页的公开目录里，但按标识仍能直接打开。
                    # 用"公开"会让每个开发者本机的首页都多出一个假活动。
                    visibility=EventVisibility.INVISIBLE.value,
                    # 免登录、不覆盖名额上限：调试台默认要能直接提交成功，
                    # 否则第一次用就得先去管理台改配置。
                    submission_requires_login=False,
                )
            )
            session.flush()
    except IntegrityError:
        # 并发启动时另一个进程已经建好了。唯一约束才是权威，不是上面那次查询 ——
        # 两次启动可能都读到"不存在"。
        return DevSeedOutcome(False, event_id, skipped_reason=SKIP_ALREADY_EXISTS)

    return DevSeedOutcome(True, event_id)


def log_dev_seed_outcome(
    outcome: DevSeedOutcome, target: logging.Logger | None = None
) -> None:
    log = target or logger

    if outcome.created:
        log.info(
            "开发模式：已补齐调试活动 %r（不可见、免登录）。"
            "调试台可用 /develop?src=/draft/<名字>/index.html 打开。",
            outcome.event_id,
        )
        return

    if outcome.skipped_reason == SKIP_ALREADY_EXISTS:
        log.debug("开发模式：调试活动 %r 已存在，保持原样", outcome.event_id)
    elif outcome.skipped_reason == SKIP_NOT_DEVELOPMENT:
        log.debug("非开发模式：不补齐调试活动")
