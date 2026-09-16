from __future__ import annotations

import json
import re
from collections import deque
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import WritingGraphRelease, WritingGraphReleaseItem


WRITING_OBJECT_TYPES = {"evidence", "entity", "claim", "fact", "relation"}


def _active(model: type) -> Any:
    return model.deleted_at.is_(None)


def _plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _tokens(value: str) -> set[str]:
    normalized = value.casefold().strip()
    words = set(re.findall(r"[a-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    words.update(
        normalized[index:index + 2]
        for index in range(max(0, len(normalized) - 1))
        if "\u4e00" <= normalized[index] <= "\u9fff"
        and "\u4e00" <= normalized[index + 1] <= "\u9fff"
    )
    return {item for item in words if item}


def release_snapshots(
    db: Session,
    release: WritingGraphRelease,
    object_types: Iterable[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    requested = set(object_types or WRITING_OBJECT_TYPES)
    if requested - WRITING_OBJECT_TYPES:
        raise ValueError("写作图谱查询包含未知对象类型")
    rows = list(db.scalars(select(WritingGraphReleaseItem).where(
        WritingGraphReleaseItem.release_id == release.id,
        WritingGraphReleaseItem.object_type.in_(sorted(requested)),
        _active(WritingGraphReleaseItem),
    )))
    return [(row.object_type, dict(row.snapshot or {})) for row in rows]


def search_writing_graph_release(
    db: Session,
    release: WritingGraphRelease,
    *,
    query: str,
    object_types: Iterable[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search immutable snapshots only; candidates can never leak into writing."""

    query_text = query.casefold().strip()
    query_tokens = _tokens(query)
    requested = set(object_types or WRITING_OBJECT_TYPES)
    all_snapshots = release_snapshots(db, release, WRITING_OBJECT_TYPES)
    by_key = {
        (object_type, str(snapshot.get("id") or "")): snapshot
        for object_type, snapshot in all_snapshots
    }

    def linked_text(object_type: str, snapshot: dict[str, Any]) -> str:
        linked: list[Any] = [snapshot]
        if object_type in {"fact", "relation"}:
            for entity_id in (
                snapshot.get("subject_candidate_id") or snapshot.get("subject_entity_id"),
                snapshot.get("object_candidate_id") or snapshot.get("object_entity_id"),
            ):
                if entity_id and ("entity", str(entity_id)) in by_key:
                    linked.append(by_key[("entity", str(entity_id))])
        if object_type == "fact":
            linked.extend(
                by_key[("claim", str(item))]
                for item in snapshot.get("claim_ids") or []
                if ("claim", str(item)) in by_key
            )
            linked.extend(
                by_key[("evidence", str(item))]
                for item in snapshot.get("evidence_ids") or []
                if ("evidence", str(item)) in by_key
            )
        if object_type == "relation" and snapshot.get("fact_id"):
            fact = by_key.get(("fact", str(snapshot["fact_id"])))
            if fact:
                linked.append(fact)
                linked.extend(
                    by_key[("evidence", str(item))]
                    for item in fact.get("evidence_ids") or []
                    if ("evidence", str(item)) in by_key
                )
        return _plain(linked).casefold()

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for object_type, snapshot in all_snapshots:
        if object_type not in requested:
            continue
        haystack = linked_text(object_type, snapshot)
        haystack_tokens = _tokens(haystack)
        overlap = len(query_tokens & haystack_tokens)
        score = float(overlap)
        if query_text and query_text in haystack:
            score += 10.0
        if not query_text or score <= 0:
            continue
        ranked.append((score, object_type, snapshot))
    ranked.sort(key=lambda item: (-item[0], item[1], str(item[2].get("id") or "")))
    return [
        {"object_type": object_type, "score": score, "snapshot": snapshot}
        for score, object_type, snapshot in ranked[: max(1, min(int(limit), 100))]
    ]


def get_release_object(
    db: Session,
    release: WritingGraphRelease,
    *,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    if object_type not in WRITING_OBJECT_TYPES:
        raise ValueError("写作图谱对象类型不存在")
    row = db.scalar(select(WritingGraphReleaseItem).where(
        WritingGraphReleaseItem.release_id == release.id,
        WritingGraphReleaseItem.object_type == object_type,
        WritingGraphReleaseItem.object_id == object_id,
        _active(WritingGraphReleaseItem),
    ))
    if row is None:
        raise LookupError("对象不在当前写作图谱版本中")
    return dict(row.snapshot or {})


def writing_relation_path(
    db: Session,
    release: WritingGraphRelease,
    *,
    start_entity_id: str,
    end_entity_id: str | None = None,
    max_hops: int = 4,
) -> list[dict[str, Any]]:
    snapshots = release_snapshots(db, release, {"entity", "relation"})
    entities = {item["id"]: item for kind, item in snapshots if kind == "entity"}
    if start_entity_id not in entities:
        raise LookupError("起点实体不在当前写作图谱版本中")
    if end_entity_id and end_entity_id not in entities:
        raise LookupError("终点实体不在当前写作图谱版本中")
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for kind, relation in snapshots:
        if kind != "relation":
            continue
        source = relation.get("subject_candidate_id") or relation.get("subject_entity_id")
        target = relation.get("object_candidate_id") or relation.get("object_entity_id")
        if source in entities and target in entities:
            adjacency.setdefault(str(source), []).append((str(target), relation))
    queue = deque([(start_entity_id, [])])
    visited = {start_entity_id}
    results: list[dict[str, Any]] = []
    hop_limit = max(1, min(int(max_hops), 8))
    while queue and len(results) < 50:
        current, path = queue.popleft()
        if len(path) >= hop_limit:
            continue
        for target, relation in adjacency.get(current, []):
            next_path = [*path, {
                "source": entities[current],
                "relation": relation,
                "target": entities[target],
            }]
            if end_entity_id:
                if target == end_entity_id:
                    return next_path
            else:
                results.extend(next_path[-1:])
            if target not in visited:
                visited.add(target)
                queue.append((target, next_path))
    return [] if end_entity_id else results
