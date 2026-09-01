from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    ContentElement,
    DataSourceSchemaVersion,
    Document,
    DocumentVersion,
    Fact,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
)


def _active_context(
    db: Session,
    document: Document,
) -> tuple[SourceConnector, SemanticMappingSet, SemanticMappingVersion, DataSourceSchemaVersion] | None:
    if not document.source_id:
        return None
    source = db.get(SourceConnector, document.source_id)
    if (
        source is None or source.deleted_at is not None or source.source_type != "database"
        or not bool((source.config or {}).get("graph_materialization_enabled", False))
    ):
        return None
    mapping_set = db.scalar(select(SemanticMappingSet).where(
        SemanticMappingSet.source_id == source.id,
        SemanticMappingSet.status == "active",
        SemanticMappingSet.active_version_id.is_not(None),
        SemanticMappingSet.deleted_at.is_(None),
    ).order_by(SemanticMappingSet.updated_at.desc()).limit(1))
    if mapping_set is None:
        return None
    mapping = db.get(SemanticMappingVersion, mapping_set.active_version_id)
    schema = db.get(DataSourceSchemaVersion, mapping.schema_version_id) if mapping else None
    if (
        mapping is None or mapping.status != "active" or mapping.deleted_at is not None
        or schema is None or schema.status != "current" or schema.deleted_at is not None
        or schema.schema_fingerprint != mapping.schema_fingerprint
    ):
        return None
    return source, mapping_set, mapping, schema


def _semantic_entity(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    source: SourceConnector,
    mapping_set: SemanticMappingSet,
    mapping: SemanticMappingVersion,
    entity_mapping: dict[str, Any],
    element: ContentElement,
    row: dict[str, Any],
) -> CanonicalEntity:
    from semantica.normalize import EntityNormalizer

    metadata = element.element_metadata or {}
    row_key = str(metadata.get("row_key") or "")
    fragment = entity_mapping["fragments"][0]
    display_id = fragment.get("display_column_id")
    display_name = None
    if display_id:
        display_name = row.get(display_id.rsplit(".", 1)[-1])
    display_name = str(display_name or f"{entity_mapping['label']} {row_key[:8]}")[:500]
    canonical_name = EntityNormalizer().normalize_entity(display_name, entity_type=entity_mapping["label"])
    external_identity = f"db:{source.id}:{entity_mapping['id']}:{row_key}".casefold()[:500]
    entity = db.scalar(select(CanonicalEntity).where(
        CanonicalEntity.space_id == space_id,
        CanonicalEntity.normalized_name == external_identity,
        CanonicalEntity.entity_type == entity_mapping["label"][:100],
        CanonicalEntity.deleted_at.is_(None),
    ))
    properties = {
        "external_identity": external_identity,
        "materialization": "database_mapping",
        "source_id": source.id,
        "object_id": metadata.get("object_id"),
        "row_key": row_key,
        "schema_version_id": mapping.schema_version_id,
        "mapping_set_id": mapping_set.id,
        "mapping_version_id": mapping.id,
    }
    if entity is None:
        entity = CanonicalEntity(
            tenant_id=tenant_id,
            space_id=space_id,
            canonical_name=canonical_name,
            normalized_name=external_identity,
            entity_type=entity_mapping["label"][:100],
            properties=properties,
            confidence=1.0,
            status="published",
        )
        db.add(entity)
        db.flush()
    else:
        entity.canonical_name = canonical_name
        entity.properties = {**(entity.properties or {}), **properties}
        entity.confidence = 1.0
        entity.status = "published"
    return entity


def _upsert_fact(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    subject_id: str,
    predicate: str,
    chunk_id: str,
    object_id: str | None = None,
    object_value: str | None = None,
) -> Fact:
    fact = db.scalar(select(Fact).where(
        Fact.space_id == space_id,
        Fact.subject_entity_id == subject_id,
        Fact.predicate == predicate[:200],
        Fact.object_entity_id == object_id,
        Fact.source_chunk_id == chunk_id,
    ).order_by(Fact.created_at.desc()).limit(1))
    if fact is None:
        fact = Fact(
            tenant_id=tenant_id,
            space_id=space_id,
            subject_entity_id=subject_id,
            predicate=predicate[:200],
            object_entity_id=object_id,
            object_value=object_value,
            source_chunk_id=chunk_id,
            confidence=1.0,
            status="published",
        )
        db.add(fact)
    else:
        fact.object_value = object_value
        fact.confidence = 1.0
        fact.status = "published"
        fact.deleted_at = None
    return fact


def materialize_database_mapping(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
) -> dict[str, Any]:
    context = _active_context(db, document)
    if context is None:
        return {"enabled": False, "entities": 0, "attribute_facts": 0, "relationship_facts": 0}
    source, mapping_set, mapping, schema = context
    manifest = mapping.manifest or {}
    objects = {item["id"]: item for item in (schema.catalog or {}).get("objects") or []}
    column_names = {
        column["id"]: column["name"]
        for item in objects.values()
        for column in item.get("columns") or []
    }
    elements = list(db.scalars(select(ContentElement).where(
        ContentElement.version_id == version.id,
        ContentElement.element_type == "record",
        ContentElement.deleted_at.is_(None),
    )))
    chunks_by_element = {
        item.element_id: item
        for item in db.scalars(select(Chunk).where(
            Chunk.version_id == version.id,
            Chunk.deleted_at.is_(None),
        ).order_by(Chunk.ordinal.desc()))
        if item.element_id
    }
    # A new current snapshot retires previously materialized entities for this
    # source; current rows are reactivated below. GraphRelease therefore omits
    # database rows deleted since the prior synchronization.
    for entity in db.scalars(select(CanonicalEntity).where(
        CanonicalEntity.space_id == document.space_id,
        CanonicalEntity.deleted_at.is_(None),
    )):
        if (entity.properties or {}).get("source_id") == source.id and (entity.properties or {}).get("materialization") == "database_mapping":
            entity.status = "superseded"

    element_by_object: dict[str, list[ContentElement]] = {}
    for element in elements:
        element_by_object.setdefault(str((element.element_metadata or {}).get("object_id") or ""), []).append(element)
    entity_instances: dict[tuple[str, str], CanonicalEntity] = {}
    attribute_facts = 0
    for entity_mapping in manifest.get("entities") or []:
        fragment = next((item for item in entity_mapping.get("fragments") or [] if item.get("role") == "primary"), None)
        if not fragment:
            continue
        for element in element_by_object.get(fragment["object_id"], []):
            metadata = element.element_metadata or {}
            row = metadata.get("row") or {}
            row_key = str(metadata.get("row_key") or "")
            chunk = chunks_by_element.get(element.id)
            if not row_key or chunk is None:
                continue
            entity = _semantic_entity(
                db,
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                source=source,
                mapping_set=mapping_set,
                mapping=mapping,
                entity_mapping=entity_mapping,
                element=element,
                row=row,
            )
            entity_instances[(entity_mapping["id"], row_key)] = entity
            for attribute in manifest.get("attributes") or []:
                if attribute.get("entity_id") != entity_mapping["id"] or attribute.get("fragment_id") != fragment["id"]:
                    continue
                name = column_names.get(attribute["column_id"])
                if not name or name not in row:
                    continue
                value = row.get(name)
                if value is None:
                    continue
                _upsert_fact(
                    db,
                    tenant_id=version.tenant_id,
                    space_id=document.space_id,
                    subject_id=entity.id,
                    predicate=attribute["label"],
                    chunk_id=chunk.id,
                    object_value=str(value),
                )
                attribute_facts += 1

    relationship_facts = 0
    entities_by_id = {item["id"]: item for item in manifest.get("entities") or []}
    for relationship in manifest.get("relationships") or []:
        from_mapping = entities_by_id.get(relationship.get("from_entity_id"))
        to_mapping = entities_by_id.get(relationship.get("to_entity_id"))
        if not from_mapping or not to_mapping:
            continue
        from_fragment = from_mapping.get("fragments", [])[0]
        to_fragment = to_mapping.get("fragments", [])[0]
        predicates = relationship.get("predicates") or []
        for left_element in element_by_object.get(from_fragment["object_id"], []):
            left_row = (left_element.element_metadata or {}).get("row") or {}
            left_key = str((left_element.element_metadata or {}).get("row_key") or "")
            subject = entity_instances.get((from_mapping["id"], left_key))
            chunk = chunks_by_element.get(left_element.id)
            if subject is None or chunk is None:
                continue
            for right_element in element_by_object.get(to_fragment["object_id"], []):
                right_row = (right_element.element_metadata or {}).get("row") or {}
                matches = True
                for predicate in predicates:
                    predicate_left = predicate["left"]
                    predicate_right = predicate["right"]
                    if (
                        predicate_left.get("object_id") == from_fragment["object_id"]
                        and predicate_right.get("object_id") == to_fragment["object_id"]
                    ):
                        left_column_id = predicate_left.get("column_id")
                        right_column_id = predicate_right.get("column_id")
                    elif (
                        predicate_right.get("object_id") == from_fragment["object_id"]
                        and predicate_left.get("object_id") == to_fragment["object_id"]
                    ):
                        left_column_id = predicate_right.get("column_id")
                        right_column_id = predicate_left.get("column_id")
                    else:
                        matches = False
                        break
                    left_column = column_names.get(left_column_id)
                    right_column = column_names.get(right_column_id)
                    if not left_column or not right_column or left_row.get(left_column) != right_row.get(right_column):
                        matches = False
                        break
                if not matches:
                    continue
                right_key = str((right_element.element_metadata or {}).get("row_key") or "")
                target = entity_instances.get((to_mapping["id"], right_key))
                if target is None:
                    continue
                _upsert_fact(
                    db,
                    tenant_id=version.tenant_id,
                    space_id=document.space_id,
                    subject_id=subject.id,
                    predicate=relationship["label"],
                    chunk_id=chunk.id,
                    object_id=target.id,
                )
                relationship_facts += 1
    return {
        "enabled": True,
        "mapping_version_id": mapping.id,
        "schema_version_id": schema.id,
        "entities": len(entity_instances),
        "attribute_facts": attribute_facts,
        "relationship_facts": relationship_facts,
    }
