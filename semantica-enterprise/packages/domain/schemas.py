from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BlobRef(BaseModel):
    bucket: str
    object_key: str
    filename: str
    content_type: str
    size: int = Field(ge=0)
    sha256: str


class ContentElementData(BaseModel):
    element_id: str
    element_type: Literal[
        "text",
        "title",
        "paragraph",
        "list",
        "table",
        "image",
        "slide",
        "sheet",
        "record",
        "code",
        "email",
        "attachment",
        "transcript",
        "audio",
        "video",
    ]
    ordinal: int = Field(ge=0)
    text: str = ""
    structural_path: str
    page_number: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityItem(BaseModel):
    key: str
    name: str
    available: bool
    version: str | None = None
    detail: str = ""
    required_for: str


class CapabilityReport(BaseModel):
    semantica_version: str
    checked_at: datetime = Field(default_factory=utcnow)
    ready_for_m4: bool
    items: list[CapabilityItem]


class ProcessingFailure(BaseModel):
    code: str
    message: str
    retryable: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
