from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def audit(
    db: Session,
    tenant_id: str,
    actor_id: str | None,
    action: str,
    object_type: str,
    object_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        detail=detail or {},
    )
    db.add(event)
    return event
