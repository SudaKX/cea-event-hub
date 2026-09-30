"""内容投放与清单的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class ContentFileItem(BaseModel):
    path: str
    size_bytes: int


class ContentListResponse(BaseModel):
    event_id: str
    content_version: int
    entry_path: str
    files: list[ContentFileItem]


class ContentDeployResponse(BaseModel):
    event_id: str
    file_count: int
    total_bytes: int
    content_version: int


__all__ = ["ContentDeployResponse", "ContentFileItem", "ContentListResponse"]
