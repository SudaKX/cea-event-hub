"""公开活动接口。

只暴露 live 活动；draft 与 archived 一律 404（而不是 403——后者会泄露
"这个标识存在但还没上线"）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import DbSession, RuntimeSettings
from app.schemas.events import (
    EventEnvelope,
    EventListResponse,
    to_public,
)
from app.services.events import EventService

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventListResponse, summary="公开活动列表")
def list_events(session: DbSession, settings: RuntimeSettings) -> EventListResponse:
    service = EventService(settings)
    events = service.list_public(session)
    return EventListResponse(
        events=[to_public(event, service.quota_state(event)) for event in events]
    )


@router.get("/{event_id}", response_model=EventEnvelope, summary="公开活动详情")
def get_event(
    event_id: str, session: DbSession, settings: RuntimeSettings
) -> EventEnvelope:
    service = EventService(settings)
    event = service.get_public(session, event_id)
    return EventEnvelope(event=to_public(event, service.quota_state(event)))


__all__ = ["router"]
