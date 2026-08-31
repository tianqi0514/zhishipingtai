from __future__ import annotations

from pathlib import Path
from typing import Iterable

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
