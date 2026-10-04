"""首次启动引导管理员（design.md 决策 18）。

开放注册无法产生第一个管理员，因此需要一条引导路径。

触发器刻意是**"用户表为空"**而不是"不存在名为 admin 的账号"：后者会在管理员
被有意删除后，靠一次重启悄悄复活一个账号名已知的特权账号。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.enums import UserRole
from app.core.security import generate_password, hash_password
from app.core.text import normalize_username
from app.db.models import User
from app.db.session import Database

logger = logging.getLogger(__name__)

SKIP_DISABLED = "disabled"
SKIP_USERS_EXIST = "users_exist"
SKIP_CONCURRENT = "concurrent_start"


@dataclass(frozen=True)
class BootstrapOutcome:
    created: bool
    username: str
    #: 仅当密码是本次随机生成时才有值。密码来自环境变量时为 None——
    #: 这正是提供该环境变量的意义：不让密码进入日志。
    generated_password: str | None = None
    skipped_reason: str | None = None


def ensure_bootstrap_admin(
    database: Database,
    settings: Settings,
    *,
    target_logger: logging.Logger | None = None,
) -> BootstrapOutcome:
    """用户表为空时创建管理员；否则什么都不做。

    输出密码是**本函数职责的一部分**，不由调用方负责：随机密码只存在于这次
    返回里，调用方一旦忘记输出，账号就永久进不去了。
    """
    outcome = _create_if_empty(database, settings)
    log_bootstrap_outcome(outcome, target_logger)
    return outcome


def _create_if_empty(database: Database, settings: Settings) -> BootstrapOutcome:
    username = normalize_username(settings.ADMIN_USERNAME)

    if not settings.ADMIN_BOOTSTRAP_ENABLED:
        return BootstrapOutcome(False, username, skipped_reason=SKIP_DISABLED)

    provided = settings.ADMIN_INITIAL_PASSWORD
    plaintext = provided or generate_password()

    try:
        with database.session() as session:
            if session.scalar(select(func.count()).select_from(User)):
                return BootstrapOutcome(
                    False, username, skipped_reason=SKIP_USERS_EXIST
                )
            session.add(
                User(
                    username=username,
                    display_name=username,
                    password_hash=hash_password(plaintext),
                    role=UserRole.ADMIN.value,
                    is_active=True,
                )
            )
            session.flush()
    except IntegrityError:
        # 并发启动时另一个进程已经建好了。唯一约束才是权威，不是上面那次
        # 计数检查——两次启动可能都读到 0。
        return BootstrapOutcome(False, username, skipped_reason=SKIP_CONCURRENT)

    return BootstrapOutcome(
        created=True,
        username=username,
        generated_password=None if provided else plaintext,
    )


def log_bootstrap_outcome(
    outcome: BootstrapOutcome, target: logging.Logger | None = None
) -> None:
    """输出引导结果。

    密码的输出分两种情形，这是本决策唯一需要权衡的地方：

    * 未设 ADMIN_INITIAL_PASSWORD -> 生成随机密码，在**创建的那一次**以告警
      级别输出，并提示立即改密
    * 设了 ADMIN_INITIAL_PASSWORD -> 使用该值，且输出中**完全不出现密码**

    后者是生产环境规避密码进入日志的正式手段（日志会被采集、转发、长期保留，
    不是可靠渠道）。
    """
    log = target or logger

    if not outcome.created:
        if outcome.skipped_reason == SKIP_USERS_EXIST:
            log.debug("首次启动引导：用户表非空，跳过")
        elif outcome.skipped_reason == SKIP_DISABLED:
            log.debug("首次启动引导：已通过配置关闭")
        elif outcome.skipped_reason == SKIP_CONCURRENT:
            log.info("首次启动引导：另一进程已完成创建，跳过")
        return

    if outcome.generated_password is None:
        log.info(
            "首次启动引导：已创建管理员 %r（初始密码来自环境变量，未输出）。"
            "请登录后立即修改密码。",
            outcome.username,
        )
        return

    log.warning(
        "\n"
        "======================================================================\n"
        "  首次启动引导：已创建管理员账号\n"
        "    用户名  : %s\n"
        "    初始密码: %s\n"
        "  该密码只显示这一次，请立即登录并修改。\n"
        "  若不想让密码出现在日志中，请设置 ADMIN_INITIAL_PASSWORD 后重建数据库。\n"
        "======================================================================",
        outcome.username,
        outcome.generated_password,
    )
