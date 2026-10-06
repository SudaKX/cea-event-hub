"""ORM 模型集合。

集中导入全部模型，使 `Base.metadata` 在 Alembic autogenerate 与测试建表时
都能看到完整 schema——漏掉一个模型会静默产生空的迁移。
"""

from __future__ import annotations

from app.db.models.event import Event
from app.db.models.invitation import InvitationCode, InvitationRedemption
from app.db.models.platform import PlatformSwitch
from app.db.models.submission import Submission, SubmissionFile, SubmitterQuota
from app.db.models.user import (
    PendingRegistration,
    User,
    UserSession,
    UserToken,
)

__all__ = [
    "Event",
    "InvitationCode",
    "InvitationRedemption",
    "PendingRegistration",
    "PlatformSwitch",
    "Submission",
    "SubmissionFile",
    "SubmitterQuota",
    "User",
    "UserSession",
    "UserToken",
]
