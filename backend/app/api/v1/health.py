"""系统类接口：健康检查。

这个端点回答的是"服务现在能不能干活"，而不是"进程还在不在"：它每次被调用都做一次
数据库往返，并在响应里同时给出**数据库当前的迁移版本**与**代码期望的版本**。后者让
"代码已部署、迁移没跑"这一个人为失误直接表现为两个不一致的版本号，而不必登服务器
查库。

**响应只含状态。** 它是匿名的，且不在 `/api/**` 的鉴权范围里（那是给活动页用的隔离
约定，与这里无关）—— 因此不得出现凭据、绝对路径或任何用户数据。这条由测试里的
字段白名单守着：往响应里加一个连接串就该让测试变红。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.deps import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

#: `alembic.ini` 在包外一层（backend/ 下）
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


@lru_cache(maxsize=1)
def expected_revision() -> str:
    """代码期望的迁移版本。

    从 Alembic 的**脚本目录**读，而不是查库 —— "代码期望什么"与"库里是什么"必须是
    两个独立来源，二者不一致才有信息量。进程运行期间代码不会变，因此缓存一次即可。

    读不到时返回空串而不是抛错：健康检查不该因为读不到自己的迁移目录就变成 503，
    那会把一个纯粹的运维信息问题伪装成服务故障。
    """
    try:
        script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
        return script.get_current_head() or ""
    except Exception:  # noqa: BLE001 - 见 docstring：这是信息，不是故障
        logger.warning("读不到 Alembic 脚本目录，健康检查将不报告期望版本", exc_info=True)
        return ""


def _current_revision(session: DbSession) -> str:
    """数据库当前所处的迁移版本。表不存在时返回空串。

    `alembic_version` 表不存在意味着**迁移从未跑过**（不是"连不上"）：库是可达的，
    只是里面一张业务表都没有。调用方据此报 `unmigrated` 而不是 `unreachable` ——
    后者会让人跑去查数据库进程，白费功夫。
    """
    if not inspect(session.get_bind()).has_table("alembic_version"):
        return ""
    row = session.execute(text("SELECT version_num FROM alembic_version")).first()
    return str(row[0]) if row else ""


@router.get("/health", summary="健康检查")
def health(session: DbSession) -> JSONResponse:
    """确认服务当下能否处理请求。

    三种情形分开报（见 design.md 决策 1）：连不上 → `503 unreachable`；连得上但迁移
    从未跑过 → `503 unmigrated`；迁移过但版本落后于代码 → `200`，因为落后的库通常
    仍能服务旧接口，那是"计划一次迁移"的信号而不是故障。

    **异常详情只进日志，不进响应体。** 驱动抛出的错误里常带连接串与文件路径，而这个
    端点是匿名可访问的。
    """
    common: dict[str, object] = {
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "expected_revision": expected_revision(),
    }

    try:
        revision = _current_revision(session)
    except SQLAlchemyError:
        logger.warning("健康检查：数据存储不可达", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": "unreachable",
                "revision": "",
                **common,
            },
        )

    if not revision:
        logger.warning("健康检查：数据库可达但迁移未执行")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": "unmigrated",
                "revision": "",
                **common,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "database": "ok",
            "revision": revision,
            **common,
        },
    )
