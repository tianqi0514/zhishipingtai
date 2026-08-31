from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def validate_graph(entities: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> dict[str, Any]:
    from semantica.kg.graph_validator import GraphValidator

    result = GraphValidator(strict=False).validate(
        {"entities": entities, "relationships": relationships}
    )
    issues = []
    for issue in result.issues:
        payload = asdict(issue) if is_dataclass(issue) else dict(vars(issue))
        for key, value in list(payload.items()):
            if hasattr(value, "value"):
                payload[key] = value.value
        issues.append(payload)
    return {"valid": bool(result.is_valid), "issues": issues}


def publish_graph(
    *,
    host: str,
    port: int,
    graph_name: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """Publish a versioned property graph through Semantica's FalkorDBStore."""
    from semantica.graph_store.falkordb_store import FalkorDBStore

    store = FalkorDBStore(host=host, port=port, graph_name=graph_name)
    store.connect()
    store.select_graph(graph_name)
    if entities:
        store.create_nodes(
            [
                {
                    "labels": ["Entity"],
                    "properties": {
                        "entity_id": str(item["id"]),
                        "name": str(item["name"]),
                        "entity_type": str(item["type"]),
                        "space_id": str(item.get("space_id") or ""),
                    },
                }
                for item in entities
            ]
        )
    graph = store.select_graph(graph_name)
    for item in relationships:
        graph.query(
            "MATCH (a:Entity {entity_id:$source}), (b:Entity {entity_id:$target}) "
            "CREATE (a)-[:RELATED {predicate:$predicate, fact_id:$fact_id, confidence:$confidence, origin:$origin}]->(b)",
            {
                "source": str(item["source"]),
                "target": str(item["target"]),
                "predicate": str(item["type"]),
                "fact_id": str(item.get("id") or ""),
                "confidence": float(item.get("confidence", 0)),
                "origin": str(item.get("origin") or "asserted"),
            },
        )
    store.close()
    return {"graph_name": graph_name, "entities": len(entities), "relationships": len(relationships)}


def query_graph_facts(
    *,
    host: str,
    port: int,
    graph_name: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Query fact identifiers from one published FalkorDB graph.

    FalkorDB performs graph matching only. The platform resolves returned fact
    IDs through PostgreSQL afterwards so tenant permissions and current-version
    checks remain authoritative outside the graph store.
    """
    from semantica.graph_store.falkordb_store import FalkorDBStore

    store = FalkorDBStore(host=host, port=port, graph_name=graph_name)
    try:
        store.connect()
        graph = store.select_graph(graph_name)
        result = graph.query(
            "MATCH (a:Entity)-[r:RELATED]->(b:Entity) "
            "WHERE toLower(a.name) CONTAINS $term "
            "OR $term CONTAINS toLower(a.name) "
            "OR toLower(b.name) CONTAINS $term "
            "OR $term CONTAINS toLower(b.name) "
            "OR toLower(r.predicate) CONTAINS $term "
            "RETURN r.fact_id, r.confidence LIMIT $limit",
            {"term": query.casefold().strip(), "limit": max(1, min(int(limit), 200))},
        )
        return [
            {"fact_id": str(row[0]), "score": float(row[1] or 0)}
            for row in result.result_set
            if len(row) >= 2 and row[0]
        ]
    finally:
        store.close()
