from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GovernedEntity:
    canonical_name: str
    normalized_name: str
    entity_type: str
    aliases: list[str]
    confidence: float
    mention_ids: list[str]


def govern_entities(
    mentions: list[dict[str, Any]],
    *,
    similarity_threshold: float = 0.86,
) -> tuple[list[GovernedEntity], list[dict[str, Any]]]:
    """Normalize and cluster mentions with Semantica; returns reversible merge decisions."""
    from semantica.deduplication.duplicate_detector import DuplicateDetector
    from semantica.normalize import EntityNormalizer

    normalizer = EntityNormalizer()
    normalized: list[dict[str, Any]] = []
    for item in mentions:
        name = str(item.get("text") or "").strip()
        entity_type = str(item.get("entity_type") or "其他")
        canonical = normalizer.normalize_entity(name, entity_type=entity_type, resolve_aliases=True)
        normalized.append({**item, "name": canonical, "normalized_name": canonical.casefold()})

    # Run Semantica's detector for evidence and auditability. Exact normalized
    # names are always merged; fuzzy candidates only merge above configured confidence.
    detector = DuplicateDetector(
        similarity_threshold=similarity_threshold,
        confidence_threshold=similarity_threshold,
    )
    candidates = detector.detect_duplicates(
        [{"id": str(index), "name": item["name"], "type": item["entity_type"]} for index, item in enumerate(normalized)]
    ) if len(normalized) > 1 else []
    parent = list(range(len(normalized)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    by_exact: dict[tuple[str, str], int] = {}
    for index, item in enumerate(normalized):
        key = (item["normalized_name"], item["entity_type"])
        if key in by_exact:
            union(index, by_exact[key])
        else:
            by_exact[key] = index
    decisions: list[dict[str, Any]] = []
    for candidate in candidates:
        left = int(candidate.entity1.get("id"))
        right = int(candidate.entity2.get("id"))
        if normalized[left]["entity_type"] != normalized[right]["entity_type"]:
            continue
        if float(candidate.confidence) >= similarity_threshold:
            union(left, right)
            decisions.append(
                {
                    "type": "fuzzy_merge",
                    "mention_ids": [normalized[left].get("mention_id"), normalized[right].get("mention_id")],
                    "similarity": float(candidate.similarity_score),
                    "confidence": float(candidate.confidence),
                }
            )
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, item in enumerate(normalized):
        groups.setdefault(find(index), []).append(item)
    governed: list[GovernedEntity] = []
    for items in groups.values():
        representative = max(items, key=lambda item: (float(item.get("confidence", 0)), len(item["name"])))
        governed.append(
            GovernedEntity(
                canonical_name=representative["name"][:500],
                normalized_name=representative["normalized_name"][:500],
                entity_type=representative["entity_type"][:100],
                aliases=sorted({item["text"] for item in items if item["text"] != representative["name"]}),
                confidence=max(float(item.get("confidence", 0)) for item in items),
                mention_ids=[str(item.get("mention_id")) for item in items],
            )
        )
    return governed, decisions
