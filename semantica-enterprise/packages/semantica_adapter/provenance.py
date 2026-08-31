from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from packages.domain import ContentElementData


def track_elements(
    storage_path: Path,
    *,
    version_id: str,
    source: str,
    elements: Iterable[ContentElementData],
) -> int:
    """Persist source lineage through Semantica's provenance subsystem."""
    from semantica.provenance.manager import ProvenanceManager

    storage_path.parent.mkdir(parents=True, exist_ok=True)
    manager = ProvenanceManager(storage_path=str(storage_path))
    count = 0
    for element in elements:
        manager.track_entity(
            entity_id=element.element_id,
            source=source,
            metadata={
                "version_id": version_id,
                "element_type": element.element_type,
                "ordinal": element.ordinal,
                "structural_path": element.structural_path,
                "page_number": element.page_number,
            },
            confidence=1.0,
            is_automated=True,
        )
        count += 1
    return count


def track_curation_decision(
    storage_path: Path,
    *,
    decision_id: str,
    target_id: str,
    target_type: str,
    user_id: str,
    batch_id: str,
    operation: str,
    before_value: Any,
    after_value: Any,
    source_fingerprint: str,
    supersedes_id: str | None,
) -> None:
    """Record human curation in Semantica's versioned provenance chain."""
    from semantica.provenance.manager import ProvenanceManager

    storage_path.parent.mkdir(parents=True, exist_ok=True)
    manager = ProvenanceManager(storage_path=str(storage_path))
    manager.track_entity(
        entity_id=f"curation:{decision_id}",
        source=f"platform:{target_type}:{target_id}",
        metadata={
            "decision_id": decision_id,
            "target_id": target_id,
            "target_type": target_type,
            "operation": operation,
            "before": before_value,
            "after": after_value,
            "source_fingerprint": source_fingerprint,
        },
        confidence=1.0,
        is_automated=False,
        agent_id=user_id,
        agent_type="human",
        role="curator",
        activity_id=batch_id,
        revision_type=operation,
        supersedes=f"curation:{supersedes_id}" if supersedes_id else None,
        parent_entity_id=f"platform:{target_type}:{target_id}",
    )
