from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.structured_schemas import SemanticMappingManifest
from packages.platform.models import (
    DataSourceSchemaVersion,
    Ontology,
    OntologyTerm,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
    User,
)
from packages.platform.structured_data import canonical_json, current_schema, fingerprint


def empty_manifest(source: SourceConnector, ontology: Ontology, schema: DataSourceSchemaVersion) -> dict[str, Any]:
    return SemanticMappingManifest(
        source_id=source.id,
        ontology_id=ontology.id,
        schema_version_id=schema.id,
    ).model_dump()


def seal_manifest(value: dict[str, Any]) -> tuple[dict[str, Any], str]:
    validated = SemanticMappingManifest.model_validate(value).model_dump()
    return validated, fingerprint(validated)


def create_mapping_version(
    db: Session,
    mapping_set: SemanticMappingSet,
    schema: DataSourceSchemaVersion,
    manifest: dict[str, Any],
    user: User,
    *,
    status: str = "draft",
    validation_report: dict[str, Any] | None = None,
) -> SemanticMappingVersion:
    normalized, mapping_hash = seal_manifest(manifest)
    existing = db.scalar(select(SemanticMappingVersion).where(
        SemanticMappingVersion.mapping_set_id == mapping_set.id,
        SemanticMappingVersion.mapping_hash == mapping_hash,
        SemanticMappingVersion.deleted_at.is_(None),
    ))
    if existing:
        return existing
    version_number = (db.scalar(select(func.max(SemanticMappingVersion.version_number)).where(
        SemanticMappingVersion.mapping_set_id == mapping_set.id
    )) or 0) + 1
    row = SemanticMappingVersion(
        tenant_id=mapping_set.tenant_id,
        space_id=mapping_set.space_id,
        source_id=mapping_set.source_id,
        mapping_set_id=mapping_set.id,
        schema_version_id=schema.id,
        schema_fingerprint=schema.schema_fingerprint,
        mapping_hash=mapping_hash,
        version_number=version_number,
        manifest=normalized,
        status=status,
        validation_report=validation_report or {},
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


def latest_mapping_version(db: Session, mapping_set_id: str) -> SemanticMappingVersion | None:
    return db.scalar(select(SemanticMappingVersion).where(
        SemanticMappingVersion.mapping_set_id == mapping_set_id,
        SemanticMappingVersion.deleted_at.is_(None),
    ).order_by(SemanticMappingVersion.version_number.desc()).limit(1))


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def validate_mapping_manifest(
    db: Session,
    mapping_set: SemanticMappingSet,
    version: SemanticMappingVersion,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = SemanticMappingManifest.model_validate(version.manifest)
    except Exception as exc:
        return {"ok": False, "errors": [f"映射格式无效：{exc}"], "warnings": [], "counts": {}}
    source = db.get(SourceConnector, mapping_set.source_id)
    ontology = db.get(Ontology, mapping_set.ontology_id)
    schema = db.get(DataSourceSchemaVersion, version.schema_version_id)
    current = current_schema(db, mapping_set.source_id)
    if source is None or source.deleted_at is not None:
        errors.append("数据源不存在或已删除")
    if ontology is None or ontology.deleted_at is not None or not ontology.enabled:
        errors.append("本体不存在或已停用")
    if schema is None or schema.deleted_at is not None:
        errors.append("映射绑定的 Schema 版本不存在")
    if current is None:
        errors.append("数据源尚未完成结构发现")
    elif current.id != version.schema_version_id or current.schema_fingerprint != version.schema_fingerprint:
        errors.append("映射绑定的 Schema 已过期，请基于最新结构创建映射版本")
    if manifest.source_id != mapping_set.source_id:
        errors.append("Manifest 数据源与映射集不一致")
    if manifest.ontology_id != mapping_set.ontology_id:
        errors.append("Manifest 本体与映射集不一致")
    if manifest.schema_version_id != version.schema_version_id:
        errors.append("Manifest Schema 版本与映射版本不一致")

    catalog_objects = {
        item["id"]: item for item in ((schema.catalog if schema else {}) or {}).get("objects") or []
    }
    catalog_columns = {
        column["id"]: (item, column)
        for item in catalog_objects.values()
        for column in item.get("columns") or []
    }
    terms = {
        row.id: row for row in db.scalars(select(OntologyTerm).where(
            OntologyTerm.ontology_id == mapping_set.ontology_id,
            OntologyTerm.deleted_at.is_(None),
            OntologyTerm.enabled.is_(True),
        ))
    }
    for kind, ids in (
        ("实体", [item.id for item in manifest.entities]),
        ("属性", [item.id for item in manifest.attributes]),
        ("关系", [item.id for item in manifest.relationships]),
    ):
        for duplicate in _duplicates(ids):
            errors.append(f"{kind} ID 重复：{duplicate}")

    entities = {item.id: item for item in manifest.entities}
    fragments: dict[str, tuple[Any, Any]] = {}
    for entity in manifest.entities:
        term = terms.get(entity.ontology_term_id)
        if term is None or term.term_type not in {"class", "event"}:
            errors.append(f"实体 {entity.label} 未绑定有效的类别/事件本体词条")
        for fragment in entity.fragments:
            if fragment.id in fragments:
                errors.append(f"数据片段 ID 重复：{fragment.id}")
                continue
            fragments[fragment.id] = (entity, fragment)
            object_row = catalog_objects.get(fragment.object_id)
            if object_row is None:
                errors.append(f"实体 {entity.label} 引用了不存在的数据对象：{fragment.object_id}")
                continue
            object_columns = {item["id"] for item in object_row.get("columns") or []}
            for column_id in fragment.identity_column_ids:
                if column_id not in object_columns:
                    errors.append(f"实体身份字段不存在：{column_id}")
            if fragment.display_column_id and fragment.display_column_id not in object_columns:
                errors.append(f"实体显示字段不存在：{fragment.display_column_id}")
            if not fragment.identity_column_ids:
                warnings.append(f"实体 {entity.label} 没有稳定身份，不能物化为稳定图谱实体")
            elif not set(fragment.identity_column_ids).issubset(object_columns):
                errors.append(f"实体 {entity.label} 的身份字段越出数据表范围")

    for attribute in manifest.attributes:
        entity = entities.get(attribute.entity_id)
        term = terms.get(attribute.ontology_term_id)
        if entity is None:
            errors.append(f"属性 {attribute.label} 引用了不存在的实体")
            continue
        if term is None or term.term_type not in {"property", "class"}:
            errors.append(f"属性 {attribute.label} 未绑定有效的属性/指标词条")
        found = fragments.get(attribute.fragment_id)
        if found is None or found[0].id != entity.id:
            errors.append(f"属性 {attribute.label} 的数据片段不属于目标实体")
            continue
        if attribute.column_id not in catalog_columns:
            errors.append(f"属性字段不存在：{attribute.column_id}")
        elif catalog_columns[attribute.column_id][0]["id"] != found[1].object_id:
            errors.append(f"属性字段不属于绑定的数据片段：{attribute.column_id}")

    for relationship in manifest.relationships:
        term = terms.get(relationship.ontology_term_id)
        if term is None or term.term_type != "relation":
            errors.append(f"关系 {relationship.label} 未绑定有效的关系本体词条")
        if relationship.from_entity_id not in entities or relationship.to_entity_id not in entities:
            errors.append(f"关系 {relationship.label} 的起止实体不存在")
        for predicate in relationship.predicates:
            left = catalog_columns.get(predicate.left.column_id)
            right = catalog_columns.get(predicate.right.column_id)
            if left is None or left[0]["id"] != predicate.left.object_id:
                errors.append(f"关系左侧字段不存在或不属于对象：{predicate.left.column_id}")
            if right is None or right[0]["id"] != predicate.right.object_id:
                errors.append(f"关系右侧字段不存在或不属于对象：{predicate.right.column_id}")
            if left and right and left[1].get("type_family") != right[1].get("type_family"):
                warnings.append(f"关系字段类型不同，请确认 Join 语义：{predicate.left.column_id} ↔ {predicate.right.column_id}")

    sealed_hash = fingerprint(manifest.model_dump())
    if sealed_hash != version.mapping_hash:
        errors.append("映射内容哈希校验失败")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": sorted(set(warnings)),
        "counts": {
            "entities": len(manifest.entities),
            "attributes": len(manifest.attributes),
            "relationships": len(manifest.relationships),
            "fragments": len(fragments),
        },
        "mapping_hash": sealed_hash,
        "schema_fingerprint": version.schema_fingerprint,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


def _tokens(value: str) -> set[str]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    parts = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", expanded.casefold()))
    for chinese in list(parts):
        if re.fullmatch(r"[\u3400-\u9fff]+", chinese):
            parts.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return {item for item in parts if item}


def _term_score(term: OntologyTerm, text: str) -> float:
    source = _tokens(text)
    target = _tokens(" ".join([term.code, term.label, term.definition, *(term.aliases or [])]))
    if not source or not target:
        return 0.0
    if term.label.casefold() in text.casefold() or term.code.casefold() in text.casefold():
        return 1.0
    return len(source & target) / max(1, len(target))


def _safe_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def generate_mapping_suggestions(
    db: Session,
    mapping_set: SemanticMappingSet,
    schema: DataSourceSchemaVersion,
    *,
    minimum_confidence: float = 0.65,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    terms = list(db.scalars(select(OntologyTerm).where(
        OntologyTerm.ontology_id == mapping_set.ontology_id,
        OntologyTerm.deleted_at.is_(None),
        OntologyTerm.enabled.is_(True),
    )))
    class_terms = [item for item in terms if item.term_type in {"class", "event"}]
    property_terms = [item for item in terms if item.term_type == "property"]
    relation_terms = [item for item in terms if item.term_type == "relation"]
    entities: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    object_entities: dict[str, dict[str, Any]] = {}
    objects = (schema.catalog or {}).get("objects") or []
    for object_row in objects:
        text = f"{object_row['name']} {object_row.get('comment', '')}"
        ranked = sorted(((term, _term_score(term, text)) for term in class_terms), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < minimum_confidence:
            continue
        term, score = ranked[0]
        object_id = object_row["id"]
        fragment_id = _safe_id("fragment", object_id)
        pk_ids = [f"{object_id}.{name}" for name in object_row.get("primary_key") or []]
        entity = {
            "id": _safe_id("entity", term.id, object_id),
            "ontology_term_id": term.id,
            "label": term.label,
            "description": f"由数据对象 {object_id} 建议映射",
            "fragments": [{
                "id": fragment_id,
                "object_id": object_id,
                "role": "primary",
                "identity_column_ids": pk_ids,
                "display_column_id": next((
                    column["id"] for column in object_row.get("columns") or []
                    if column["name"].casefold() in {"name", "title", "label", "名称", "标题"}
                ), None),
                "grain": f"每行代表一个{term.label}",
                "evidence": [{"kind": "name_comment_match", "score": score, "object_id": object_id}],
            }],
        }
        entities.append(entity)
        object_entities[object_id] = entity
        suggestions.append({"kind": "entity", "target_id": entity["id"], "confidence": round(score, 4), "evidence": entity["fragments"][0]["evidence"]})
        for column in object_row.get("columns") or []:
            if column.get("sensitivity") == "blocked":
                continue
            column_text = f"{column['name']} {column.get('comment', '')}"
            property_ranked = sorted(((candidate, _term_score(candidate, column_text)) for candidate in property_terms), key=lambda item: item[1], reverse=True)
            if not property_ranked or property_ranked[0][1] < minimum_confidence:
                continue
            property_term, property_score = property_ranked[0]
            attribute = {
                "id": _safe_id("attribute", property_term.id, column["id"]),
                "ontology_term_id": property_term.id,
                "entity_id": entity["id"],
                "fragment_id": fragment_id,
                "column_id": column["id"],
                "label": property_term.label,
                "semantic_type": column.get("type_family") or "unknown",
                "is_measure": bool((property_term.constraints or {}).get("measure")),
                "confidence": round(property_score, 4),
                "evidence": [{"kind": "name_comment_match", "score": property_score, "column_id": column["id"]}],
            }
            attributes.append(attribute)
            suggestions.append({"kind": "attribute", "target_id": attribute["id"], "confidence": round(property_score, 4), "evidence": attribute["evidence"]})

    fallback_relation = next((term for term in relation_terms if term.code == "related"), None)
    for object_row in objects:
        from_entity = object_entities.get(object_row["id"])
        if not from_entity:
            continue
        for foreign_key in object_row.get("foreign_keys") or []:
            target_object_id = (
                f"{foreign_key.get('referred_schema')}.{foreign_key.get('referred_table')}"
                if foreign_key.get("referred_schema") else str(foreign_key.get("referred_table") or "")
            )
            to_entity = object_entities.get(target_object_id)
            if not to_entity:
                same_schema_id = f"{object_row.get('schema')}.{foreign_key.get('referred_table')}" if object_row.get("schema") else str(foreign_key.get("referred_table") or "")
                to_entity = object_entities.get(same_schema_id)
                target_object_id = same_schema_id
            if not to_entity or not relation_terms:
                continue
            relation_text = f"{foreign_key.get('name', '')} {object_row['name']} {foreign_key.get('referred_table', '')}"
            ranked = sorted(((term, _term_score(term, relation_text)) for term in relation_terms), key=lambda item: item[1], reverse=True)
            relation_term, score = ranked[0]
            if score < minimum_confidence:
                relation_term, score = fallback_relation or relation_term, 0.7 if fallback_relation else score
            if score < minimum_confidence:
                continue
            predicates = []
            for left, right in zip(
                foreign_key.get("constrained_columns") or [],
                foreign_key.get("referred_columns") or [],
            ):
                predicates.append({
                    "left": {"object_id": object_row["id"], "column_id": f"{object_row['id']}.{left}"},
                    "operator": "=",
                    "right": {"object_id": target_object_id, "column_id": f"{target_object_id}.{right}"},
                })
            if not predicates:
                continue
            relation = {
                "id": _safe_id("relationship", object_row["id"], target_object_id, relation_term.id),
                "ontology_term_id": relation_term.id,
                "label": relation_term.label,
                "from_entity_id": from_entity["id"],
                "to_entity_id": to_entity["id"],
                "predicates": predicates,
                "cardinality": "many_to_one",
                "confidence": round(score, 4),
                "evidence": [{"kind": "declared_foreign_key", "name": foreign_key.get("name")}],
            }
            relationships.append(relation)
            suggestions.append({"kind": "relationship", "target_id": relation["id"], "confidence": round(score, 4), "evidence": relation["evidence"]})

    manifest = SemanticMappingManifest(
        source_id=mapping_set.source_id,
        ontology_id=mapping_set.ontology_id,
        schema_version_id=schema.id,
        entities=entities,
        attributes=attributes,
        relationships=relationships,
        notes=["自动建议仅创建草稿；需通过确定性校验并由管理员激活。"],
    ).model_dump()
    return manifest, suggestions


def mapping_diff(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("entities", "attributes", "relationships"):
        old = {item["id"]: item for item in left.get(key) or []}
        new = {item["id"]: item for item in right.get(key) or []}
        result[key] = {
            "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)),
            "changed": sorted(item for item in set(old) & set(new) if canonical_json(old[item]) != canonical_json(new[item])),
        }
    result["notes_changed"] = canonical_json(left.get("notes") or []) != canonical_json(right.get("notes") or [])
    result["summary"] = {
        "added": sum(len(result[key]["added"]) for key in ("entities", "attributes", "relationships")),
        "removed": sum(len(result[key]["removed"]) for key in ("entities", "attributes", "relationships")),
        "changed": sum(len(result[key]["changed"]) for key in ("entities", "attributes", "relationships")),
        "notes_changed": result["notes_changed"],
    }
    return result
