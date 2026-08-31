"""Validation helpers for SKOS graph relationships."""

from collections import defaultdict
from typing import Iterable, Mapping


_HIERARCHY_EDGE_TYPES = frozenset({"skos:broader", "skos:narrower"})


def is_skos_hierarchy_edge(edge: Mapping[str, object]) -> bool:
    """Return whether an edge uses a SKOS hierarchy predicate."""
    if not isinstance(edge, Mapping):
        return False
    return any(
        edge.get(key) in _HIERARCHY_EDGE_TYPES
        for key in ("type", "edge_type", "relationship", "predicate", "relation")
    )


def _child_parent(edge: Mapping[str, object]) -> tuple[str, str] | None:
    """Normalize a SKOS hierarchy edge to a ``(child, parent)`` pair, or ``None``."""
    if not isinstance(edge, Mapping) or not is_skos_hierarchy_edge(edge):
        return None
    edge_type = next(
        (
            edge.get(key)
            for key in ("type", "edge_type", "relationship", "predicate", "relation")
            if edge.get(key) in _HIERARCHY_EDGE_TYPES
        ),
        None,
    )
    if edge_type not in _HIERARCHY_EDGE_TYPES:
        return None

    raw_source = edge.get("source", edge.get("source_id"))
    raw_target = edge.get("target", edge.get("target_id"))
    if raw_source is None or raw_target is None:
        return None
    source = str(raw_source).strip()
    target = str(raw_target).strip()
    if not source or not target:
        return None

    return (source, target) if edge_type == "skos:broader" else (target, source)


def validate_skos_hierarchy(
    new_edges: Iterable[Mapping[str, object]],
    existing_edges: Iterable[Mapping[str, object]] = (),
) -> None:
    """Raise ``ValueError`` when adding ``new_edges`` would introduce a cycle.

    ``skos:broader`` points from a concept to its parent while
    ``skos:narrower`` expresses the same relationship in the opposite
    direction. Both forms are normalized to child-to-parent adjacency before
    cycle detection.

    ``existing_edges`` supplies the SKOS hierarchy edges already persisted in
    the graph so that cycles spanning old and new edges are still caught.
    Only the concepts touched by ``new_edges`` are checked, though: a cycle
    that already exists entirely within ``existing_edges`` must not block an
    unrelated write elsewhere in the graph.
    """
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in existing_edges:
        pair = _child_parent(edge)
        if pair is not None:
            parents[pair[0]].add(pair[1])

    touched: set[str] = set()
    for edge in new_edges:
        pair = _child_parent(edge)
        if pair is None:
            continue
        child, parent = pair
        parents[child].add(parent)
        touched.add(child)
        touched.add(parent)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(concept: str) -> None:
        if concept in visiting:
            raise ValueError(f"SKOS hierarchy contains a cycle involving '{concept}'.")
        if concept in visited:
            return

        visiting.add(concept)
        for parent in parents.get(concept, ()):
            visit(parent)
        visiting.remove(concept)
        visited.add(concept)

    for concept in touched:
        visit(concept)
