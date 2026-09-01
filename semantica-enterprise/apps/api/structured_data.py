from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, has_space_permission, require_space_permission
from apps.api.structured_schemas import (
    DataPreviewCountRequest,
    DataPreviewPolicyUpdate,
    DataPreviewRequest,
    MappingRollbackRequest,
    MappingSuggestionRequest,
    SemanticMappingCreate,
    SemanticMappingUpdate,
    StructuredCompileRequest,
    StructuredExecuteRequest,
    StructuredIRValidationRequest,
    StructuredNaturalLanguageRequest,
    StructuredPlanValidationRequest,
)
from apps.api.utils import serialize_row
from packages.platform.audit import audit
from packages.platform.database import get_db
from packages.platform.models import (
    DataPreviewPolicy,
    DataSourceSchemaVersion,
    Chunk,
    ContentElement,
    Document,
    Fact,
    ModelConfig,
    Ontology,
    Conversation,
    ConversationMessage,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
    StructuredQueryCitation,
    StructuredQueryRun,
    User,
)
from packages.platform.semantic_mapping import (
    create_mapping_version,
    empty_manifest,
    generate_mapping_suggestions,
    latest_mapping_version,
    mapping_diff,
    validate_mapping_manifest,
)
from packages.platform.structured_data import (
    StructuredDataError,
    current_schema,
    discover_catalog,
    exact_count,
    get_or_create_preview_policy,
    persist_discovery,
    preview_live,
    preview_snapshot,
    inspect_distinct_values,
)
from packages.platform.structured_query import (
    cancel_active_query,
    compile_structured_query,
    execute_compiled_query,
    generate_semantic_plan_ir,
    collect_semantic_value_hints,
    validate_ir,
    validate_plan,
)
from packages.platform.security import decrypt_secret


router = APIRouter(tags=["structured-data"])


def _source(db: Session, source_id: str, user: User, permission: str = "read") -> SourceConnector:
    row = db.get(SourceConnector, source_id)
    if row is None or row.deleted_at is not None or row.tenant_id != user.tenant_id:
        raise HTTPException(404, "数据源不存在")
    require_space_permission(db, user, row.space_id, permission)
    if row.source_type != "database":
        raise HTTPException(409, "该功能仅支持 MySQL 和 PostgreSQL 数据源")
    return row


def _raise_structured(exc: StructuredDataError) -> None:
    raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


def _schema_payload(row: DataSourceSchemaVersion, *, include_catalog: bool = True) -> dict[str, Any]:
    value = serialize_row(row)
    if not include_catalog:
        value.pop("catalog", None)
    return value


@router.post("/sources/{source_id}/schema/discover")
def discover_source_schema(
    source_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user, "write")
    try:
        catalog = discover_catalog(source)
        row, changed = persist_discovery(db, source, catalog)
    except StructuredDataError as exc:
        _raise_structured(exc)
    except Exception as exc:
        raise HTTPException(400, {"code": "SCHEMA_DISCOVERY_FAILED", "message": f"结构发现失败：{type(exc).__name__}"}) from exc
    get_or_create_preview_policy(db, source)
    audit(
        db,
        user.tenant_id,
        user.id,
        "source.schema.discover",
        "source",
        source.id,
        {
            "schema_version_id": row.id,
            "schema_fingerprint": row.schema_fingerprint,
            "changed": changed,
            "object_count": row.object_count,
            "column_count": row.column_count,
        },
    )
    db.commit()
    return {**_schema_payload(row), "changed": changed}


@router.get("/sources/{source_id}/schema/versions")
def list_source_schema_versions(
    source_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    rows = db.scalars(select(DataSourceSchemaVersion).where(
        DataSourceSchemaVersion.source_id == source.id,
        DataSourceSchemaVersion.deleted_at.is_(None),
    ).order_by(DataSourceSchemaVersion.version_number.desc()).limit(limit))
    return {"items": [_schema_payload(row, include_catalog=False) for row in rows]}


@router.get("/sources/{source_id}/schema/diff")
def get_source_schema_diff(
    source_id: str,
    version_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    row = db.get(DataSourceSchemaVersion, version_id) if version_id else current_schema(db, source.id)
    if row is None or row.source_id != source.id or row.deleted_at is not None:
        raise HTTPException(404, "Schema 版本不存在")
    return {
        "schema_version_id": row.id,
        "version_number": row.version_number,
        "schema_fingerprint": row.schema_fingerprint,
        "diff": row.diff_from_previous or {},
    }


@router.get("/sources/{source_id}/schema/objects")
@router.get("/sources/{source_id}/data-objects")
def list_source_data_objects(
    source_id: str,
    query: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    row = current_schema(db, source.id)
    if row is None:
        raise HTTPException(409, "请先执行结构发现")
    objects = list((row.catalog or {}).get("objects") or [])
    if query:
        needle = query.casefold().strip()
        objects = [item for item in objects if needle in f"{item.get('id')} {item.get('comment', '')}".casefold()]
    return {
        "schema_version_id": row.id,
        "schema_fingerprint": row.schema_fingerprint,
        "discovered_at": row.discovered_at,
        "dialect": (row.catalog or {}).get("dialect"),
        "database": (row.catalog or {}).get("database"),
        "objects": objects,
    }


@router.get("/sources/{source_id}/preview/config")
def get_preview_config(
    source_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    policy = get_or_create_preview_policy(db, source)
    db.commit()
    return serialize_row(policy)


@router.put("/sources/{source_id}/preview/config")
def update_preview_config(
    source_id: str,
    payload: DataPreviewPolicyUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user, "write")
    policy = get_or_create_preview_policy(db, source)
    values = payload.model_dump()
    for key, value in values.items():
        setattr(policy, key, value)
    audit(
        db,
        user.tenant_id,
        user.id,
        "source.preview_policy.update",
        "source",
        source.id,
        {
            "live_preview_enabled": policy.live_preview_enabled,
            "allowed_object_count": len(policy.allowed_objects or []),
            "denied_object_count": len(policy.denied_objects or []),
            "max_page_size": policy.max_page_size,
            "allow_exact_count": policy.allow_exact_count,
        },
    )
    db.commit()
    return serialize_row(policy)


@router.post("/sources/{source_id}/data-preview")
def preview_source_data(
    source_id: str,
    payload: DataPreviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    schema = current_schema(db, source.id)
    if schema is None:
        raise HTTPException(409, "请先执行结构发现")
    policy = get_or_create_preview_policy(db, source)
    filters = [item.model_dump() for item in payload.filters]
    try:
        if payload.mode == "snapshot":
            result = preview_snapshot(
                db, source, schema, policy,
                object_id=payload.object_id,
                page=payload.page,
                page_size=min(payload.page_size, policy.max_page_size),
            )
        else:
            result = preview_live(
                source, schema, policy,
                object_id=payload.object_id,
                page=payload.page,
                page_size=payload.page_size,
                order_by=payload.order_by,
                order_direction=payload.order_direction,
                filters=filters,
            )
    except StructuredDataError as exc:
        _raise_structured(exc)
    audit(
        db,
        user.tenant_id,
        user.id,
        "source.data.preview",
        "source",
        source.id,
        {
            "object_id": payload.object_id,
            "mode": payload.mode,
            "page": payload.page,
            "page_size": payload.page_size,
            "filter_count": len(payload.filters),
            "returned_rows": result["current_page_rows"],
            "elapsed_ms": result.get("elapsed_ms"),
        },
    )
    db.commit()
    return result


@router.post("/sources/{source_id}/data-preview/count")
def count_source_data(
    source_id: str,
    payload: DataPreviewCountRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    schema = current_schema(db, source.id)
    if schema is None:
        raise HTTPException(409, "请先执行结构发现")
    policy = get_or_create_preview_policy(db, source)
    try:
        result = exact_count(
            source, schema, policy,
            object_id=payload.object_id,
            filters=[item.model_dump() for item in payload.filters],
        )
    except StructuredDataError as exc:
        _raise_structured(exc)
    audit(
        db, user.tenant_id, user.id, "source.data.count", "source", source.id,
        {"object_id": payload.object_id, "filter_count": len(payload.filters), "elapsed_ms": result["elapsed_ms"]},
    )
    db.commit()
    return result


@router.get("/sources/{source_id}/rows/{row_key}/knowledge-status")
def get_row_knowledge_status(
    source_id: str,
    row_key: str,
    object_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, source_id, user)
    if len(row_key) != 64 or any(character not in "0123456789abcdef" for character in row_key.casefold()):
        raise HTTPException(422, "行标识格式无效")
    documents = list(db.scalars(select(Document).where(
        Document.source_id == source.id,
        Document.deleted_at.is_(None),
    ).order_by(Document.updated_at.desc())))
    document_ids = [row.id for row in documents]
    element = db.scalar(select(ContentElement).where(
        ContentElement.document_id.in_(document_ids),
        ContentElement.element_type == "record",
        ContentElement.deleted_at.is_(None),
    ).order_by(ContentElement.created_at.desc())) if document_ids else None
    if element is not None:
        candidates = db.scalars(select(ContentElement).where(
            ContentElement.document_id.in_(document_ids),
            ContentElement.element_type == "record",
            ContentElement.deleted_at.is_(None),
        ).order_by(ContentElement.created_at.desc()))
        element = next((item for item in candidates if (
            (item.element_metadata or {}).get("row_key") == row_key
            and (not object_id or (item.element_metadata or {}).get("object_id") == object_id)
        )), None)
    chunks = list(db.scalars(select(Chunk).where(
        Chunk.element_id == element.id,
        Chunk.deleted_at.is_(None),
    ))) if element else []
    chunk_ids = [row.id for row in chunks]
    fact_count = db.scalar(select(func.count()).select_from(Fact).where(
        Fact.source_chunk_id.in_(chunk_ids),
        Fact.deleted_at.is_(None),
    )) if chunk_ids else 0
    document = next((row for row in documents if element and row.id == element.document_id), None)
    return {
        "source_id": source.id,
        "object_id": object_id or ((element.element_metadata or {}).get("object_id") if element else None),
        "row_key": row_key,
        "synced": element is not None,
        "document_id": document.id if document else None,
        "version_id": element.version_id if element else None,
        "content_element_id": element.id if element else None,
        "chunk_ids": [row.id for row in chunks],
        "chunk_count": len(chunks),
        "fulltext_indexed": any(row.status in {"indexed", "published"} for row in chunks),
        "vector_indexed": any(row.status in {"indexed", "published"} for row in chunks),
        "graph_fact_count": int(fact_count or 0),
        "semantic_mapping": None,
        "message": None if element else "尚未同步或尚未加工",
    }


def _mapping_set(db: Session, mapping_id: str, user: User, permission: str = "read") -> SemanticMappingSet:
    row = db.get(SemanticMappingSet, mapping_id)
    if row is None or row.deleted_at is not None or row.tenant_id != user.tenant_id:
        raise HTTPException(404, "语义映射不存在")
    require_space_permission(db, user, row.space_id, permission)
    return row


def _mapping_payload(db: Session, row: SemanticMappingSet, *, detail: bool = False) -> dict[str, Any]:
    value = serialize_row(row)
    latest = latest_mapping_version(db, row.id)
    value["latest_version"] = serialize_row(latest) if latest else None
    if row.active_version_id:
        active = db.get(SemanticMappingVersion, row.active_version_id)
        value["active_version"] = serialize_row(active) if active else None
    else:
        value["active_version"] = None
    if detail:
        value["versions"] = [
            serialize_row(item) for item in db.scalars(select(SemanticMappingVersion).where(
                SemanticMappingVersion.mapping_set_id == row.id,
                SemanticMappingVersion.deleted_at.is_(None),
            ).order_by(SemanticMappingVersion.version_number.desc()))
        ]
    return value


@router.post("/semantic-mappings")
def create_semantic_mapping(
    payload: SemanticMappingCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _source(db, payload.source_id, user, "write")
    ontology = db.get(Ontology, payload.ontology_id)
    if (
        ontology is None or ontology.deleted_at is not None or ontology.tenant_id != user.tenant_id
        or (ontology.space_id and ontology.space_id != source.space_id)
    ):
        raise HTTPException(404, "本体不存在或不适用于该知识空间")
    schema = current_schema(db, source.id)
    if schema is None:
        raise HTTPException(409, "请先执行结构发现")
    mapping_set = SemanticMappingSet(
        tenant_id=user.tenant_id,
        space_id=source.space_id,
        source_id=source.id,
        ontology_id=ontology.id,
        name=payload.name.strip(),
        description=payload.description,
    )
    db.add(mapping_set)
    db.flush()
    manifest = payload.manifest.model_dump() if payload.manifest else empty_manifest(source, ontology, schema)
    if manifest["source_id"] != source.id or manifest["ontology_id"] != ontology.id or manifest["schema_version_id"] != schema.id:
        raise HTTPException(400, "映射内容与所选数据源、本体或 Schema 版本不一致")
    version = create_mapping_version(db, mapping_set, schema, manifest, user)
    audit(db, user.tenant_id, user.id, "semantic_mapping.create", "semantic_mapping", mapping_set.id, {"version_id": version.id})
    db.commit()
    return _mapping_payload(db, mapping_set, detail=True)


@router.get("/semantic-mappings")
def list_semantic_mappings(
    source_id: str | None = None,
    space_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(SemanticMappingSet).where(
        SemanticMappingSet.tenant_id == user.tenant_id,
        SemanticMappingSet.deleted_at.is_(None),
    ).order_by(SemanticMappingSet.updated_at.desc())
    if source_id:
        source = _source(db, source_id, user)
        query = query.where(SemanticMappingSet.source_id == source.id)
    if space_id:
        require_space_permission(db, user, space_id, "read")
        query = query.where(SemanticMappingSet.space_id == space_id)
    return {
        "items": [
            _mapping_payload(db, row)
            for row in db.scalars(query)
            if has_space_permission(db, user, row.space_id, "read")
        ]
    }


@router.get("/semantic-mappings/{mapping_id}")
def get_semantic_mapping(
    mapping_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _mapping_payload(db, _mapping_set(db, mapping_id, user), detail=True)


@router.put("/semantic-mappings/{mapping_id}")
def update_semantic_mapping(
    mapping_id: str,
    payload: SemanticMappingUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user, "write")
    if payload.name is not None:
        mapping_set.name = payload.name.strip()
    if payload.description is not None:
        mapping_set.description = payload.description
    version = None
    if payload.manifest is not None:
        schema = current_schema(db, mapping_set.source_id)
        if schema is None:
            raise HTTPException(409, "请先执行结构发现")
        manifest = payload.manifest.model_dump()
        if manifest["source_id"] != mapping_set.source_id or manifest["ontology_id"] != mapping_set.ontology_id or manifest["schema_version_id"] != schema.id:
            raise HTTPException(400, "映射内容与映射集或当前 Schema 不一致")
        version = create_mapping_version(db, mapping_set, schema, manifest, user)
        mapping_set.status = "draft"
    audit(db, user.tenant_id, user.id, "semantic_mapping.update", "semantic_mapping", mapping_set.id, {"version_id": version.id if version else None})
    db.commit()
    return _mapping_payload(db, mapping_set, detail=True)


@router.delete("/semantic-mappings/{mapping_id}")
def delete_semantic_mapping(
    mapping_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user, "manage")
    now = datetime.now(timezone.utc)
    for version in db.scalars(select(SemanticMappingVersion).where(
        SemanticMappingVersion.mapping_set_id == mapping_set.id,
        SemanticMappingVersion.deleted_at.is_(None),
    )):
        version.status = "retired"
        version.deleted_at = now
    mapping_set.status = "retired"
    mapping_set.deleted_at = now
    audit(db, user.tenant_id, user.id, "semantic_mapping.delete", "semantic_mapping", mapping_set.id)
    db.commit()
    return {"ok": True}


@router.post("/semantic-mappings/{mapping_id}/generate-suggestions")
def generate_semantic_mapping_suggestions(
    mapping_id: str,
    payload: MappingSuggestionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user, "write")
    schema = current_schema(db, mapping_set.source_id)
    if schema is None:
        raise HTTPException(409, "请先执行结构发现")
    manifest, suggestions = generate_mapping_suggestions(
        db, mapping_set, schema, minimum_confidence=payload.minimum_confidence
    )
    version = create_mapping_version(db, mapping_set, schema, manifest, user)
    mapping_set.status = "draft"
    audit(db, user.tenant_id, user.id, "semantic_mapping.suggest", "semantic_mapping", mapping_set.id, {"version_id": version.id, "suggestion_count": len(suggestions)})
    db.commit()
    return {"version": serialize_row(version), "suggestions": suggestions}


@router.post("/semantic-mappings/{mapping_id}/validate")
def validate_semantic_mapping(
    mapping_id: str,
    version_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user, "write")
    version = db.get(SemanticMappingVersion, version_id) if version_id else latest_mapping_version(db, mapping_set.id)
    if version is None or version.mapping_set_id != mapping_set.id or version.deleted_at is not None:
        raise HTTPException(404, "映射版本不存在")
    version.status = "validating"
    report = validate_mapping_manifest(db, mapping_set, version)
    version.validation_report = report
    version.status = "draft" if report["ok"] else "failed"
    audit(db, user.tenant_id, user.id, "semantic_mapping.validate", "semantic_mapping", mapping_set.id, {"version_id": version.id, "ok": report["ok"]})
    db.commit()
    return {"version": serialize_row(version), "validation": report}


@router.post("/semantic-mappings/{mapping_id}/activate")
def activate_semantic_mapping(
    mapping_id: str,
    version_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user, "manage")
    if not user.is_admin:
        raise HTTPException(403, "只有管理员可以激活语义映射")
    version = db.get(SemanticMappingVersion, version_id) if version_id else latest_mapping_version(db, mapping_set.id)
    if version is None or version.mapping_set_id != mapping_set.id or version.deleted_at is not None:
        raise HTTPException(404, "映射版本不存在")
    report = validate_mapping_manifest(db, mapping_set, version)
    if not report["ok"]:
        version.validation_report = report
        version.status = "failed"
        db.commit()
        raise HTTPException(409, {"code": "MAPPING_VALIDATION_FAILED", "message": "映射校验未通过", "validation": report})
    for old in db.scalars(select(SemanticMappingVersion).where(
        SemanticMappingVersion.mapping_set_id == mapping_set.id,
        SemanticMappingVersion.status == "active",
        SemanticMappingVersion.deleted_at.is_(None),
    )):
        old.status = "retired"
    version.status = "active"
    version.validation_report = report
    version.activated_by = user.id
    version.activated_at = datetime.now(timezone.utc)
    mapping_set.active_version_id = version.id
    mapping_set.status = "active"
    audit(db, user.tenant_id, user.id, "semantic_mapping.activate", "semantic_mapping", mapping_set.id, {"version_id": version.id, "mapping_hash": version.mapping_hash})
    db.commit()
    return _mapping_payload(db, mapping_set, detail=True)


@router.post("/semantic-mappings/{mapping_id}/rollback")
def rollback_semantic_mapping(
    mapping_id: str,
    payload: MappingRollbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user, "manage")
    if not user.is_admin:
        raise HTTPException(403, "只有管理员可以回滚语义映射")
    target = db.get(SemanticMappingVersion, payload.version_id)
    schema = current_schema(db, mapping_set.source_id)
    if target is None or target.mapping_set_id != mapping_set.id or target.deleted_at is not None:
        raise HTTPException(404, "目标映射版本不存在")
    if schema is None or target.schema_fingerprint != schema.schema_fingerprint:
        raise HTTPException(409, "目标版本绑定的 Schema 已过期，不能直接回滚")
    version = create_mapping_version(db, mapping_set, schema, target.manifest, user)
    report = validate_mapping_manifest(db, mapping_set, version)
    if not report["ok"]:
        raise HTTPException(409, {"code": "MAPPING_ROLLBACK_INVALID", "message": "目标版本已不能通过校验", "validation": report})
    for old in db.scalars(select(SemanticMappingVersion).where(
        SemanticMappingVersion.mapping_set_id == mapping_set.id,
        SemanticMappingVersion.status == "active",
        SemanticMappingVersion.deleted_at.is_(None),
    )):
        old.status = "retired"
    version.status = "active"
    version.validation_report = report
    version.activated_by = user.id
    version.activated_at = datetime.now(timezone.utc)
    mapping_set.active_version_id = version.id
    mapping_set.status = "active"
    audit(db, user.tenant_id, user.id, "semantic_mapping.rollback", "semantic_mapping", mapping_set.id, {"from_version_id": target.id, "active_version_id": version.id})
    db.commit()
    return _mapping_payload(db, mapping_set, detail=True)


@router.get("/semantic-mappings/{mapping_id}/versions")
def list_semantic_mapping_versions(
    mapping_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user)
    rows = db.scalars(select(SemanticMappingVersion).where(
        SemanticMappingVersion.mapping_set_id == mapping_set.id,
        SemanticMappingVersion.deleted_at.is_(None),
    ).order_by(SemanticMappingVersion.version_number.desc()))
    return {"items": [serialize_row(row) for row in rows]}


@router.get("/semantic-mappings/{mapping_id}/diff")
def get_semantic_mapping_diff(
    mapping_id: str,
    left_version_id: str,
    right_version_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping_set = _mapping_set(db, mapping_id, user)
    left = db.get(SemanticMappingVersion, left_version_id)
    right = db.get(SemanticMappingVersion, right_version_id)
    if not left or not right or left.mapping_set_id != mapping_set.id or right.mapping_set_id != mapping_set.id:
        raise HTTPException(404, "映射版本不存在")
    return {
        "left_version_id": left.id,
        "right_version_id": right.id,
        "diff": mapping_diff(left.manifest or {}, right.manifest or {}),
    }


def _query_context(
    db: Session,
    mapping_version_id: str,
    user: User,
    permission: str = "read",
) -> tuple[SemanticMappingSet, SemanticMappingVersion, SourceConnector, DataSourceSchemaVersion]:
    version = db.get(SemanticMappingVersion, mapping_version_id)
    if version is None or version.deleted_at is not None or version.tenant_id != user.tenant_id:
        raise HTTPException(404, "映射版本不存在")
    mapping_set = _mapping_set(db, version.mapping_set_id, user, permission)
    source = _source(db, mapping_set.source_id, user, permission)
    schema = db.get(DataSourceSchemaVersion, version.schema_version_id)
    if schema is None or schema.deleted_at is not None or schema.source_id != source.id:
        raise HTTPException(409, "映射绑定的 Schema 版本不可用")
    current = current_schema(db, source.id)
    if version.status != "active" or mapping_set.active_version_id != version.id:
        raise HTTPException(409, {"code": "MAPPING_NOT_ACTIVE", "message": "只能使用当前已激活映射"})
    if current is None or current.id != schema.id or current.schema_fingerprint != version.schema_fingerprint:
        raise HTTPException(409, {"code": "MAPPING_STALE", "message": "Schema 已变化，请重新验证并激活映射"})
    return mapping_set, version, source, schema


@router.post("/structured-query/plan/validate")
def validate_structured_plan(
    payload: StructuredPlanValidationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, version, _, _ = _query_context(db, payload.mapping_version_id, user)
    report = validate_plan(payload.plan, version)
    audit(db, user.tenant_id, user.id, "structured_query.plan.validate", "semantic_mapping_version", version.id, {"ok": report["ok"], "plan_fingerprint": report["plan_fingerprint"]})
    db.commit()
    return report


@router.post("/structured-query/ir/validate")
def validate_structured_ir(
    payload: StructuredIRValidationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, version, _, _ = _query_context(db, payload.mapping_version_id, user)
    report = validate_ir(payload.query_ir, payload.plan, version)
    audit(db, user.tenant_id, user.id, "structured_query.ir.validate", "semantic_mapping_version", version.id, {"ok": report["ok"], "ir_fingerprint": report["ir_fingerprint"]})
    db.commit()
    return report


def _compile_payload(compiled, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "validation": report,
        "dialect": compiled.dialect,
        "sql_template": compiled.sql_template,
        "parameter_summary": compiled.parameter_summary,
        "referenced_objects": compiled.referenced_objects,
        "referenced_columns": compiled.referenced_columns,
        "mapping_version_id": compiled.mapping_version_id,
        "schema_version_id": compiled.schema_version_id,
        "query_fingerprint": compiled.query_fingerprint,
    }


@router.post("/structured-query/compile")
def compile_structured_query_api(
    payload: StructuredCompileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, version, source, schema = _query_context(db, payload.mapping_version_id, user, "write")
    report = validate_ir(payload.query_ir, payload.plan, version)
    if not report["ok"]:
        raise HTTPException(422, {"code": "QUERY_IR_INVALID", "message": "查询计划或 IR 未通过校验", "validation": report})
    try:
        compiled = compile_structured_query(source, version, schema, payload.query_ir, max_rows=payload.max_rows)
    except StructuredDataError as exc:
        _raise_structured(exc)
    audit(db, user.tenant_id, user.id, "structured_query.compile", "semantic_mapping_version", version.id, {"query_fingerprint": compiled.query_fingerprint, "object_count": len(compiled.referenced_objects), "column_count": len(compiled.referenced_columns)})
    db.commit()
    return _compile_payload(compiled, report)


@router.post("/structured-query/natural-language")
def natural_language_structured_query(
    payload: StructuredNaturalLanguageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, version, source, schema = _query_context(db, payload.mapping_version_id, user)
    model = db.scalar(select(ModelConfig).where(
        ModelConfig.tenant_id == user.tenant_id,
        ModelConfig.model_kind == "llm",
        ModelConfig.enabled.is_(True),
        ModelConfig.is_default.is_(True),
        ModelConfig.deleted_at.is_(None),
    ))
    if model is None:
        raise HTTPException(409, {"code": "LLM_NOT_CONFIGURED", "message": "请先配置默认大模型"})
    api_key = decrypt_secret(model.api_key_encrypted)
    if not api_key:
        raise HTTPException(409, {"code": "LLM_KEY_NOT_CONFIGURED", "message": "默认大模型尚未配置 API Key"})
    config = model.config or {}
    policy = get_or_create_preview_policy(db, source)
    value_hints = collect_semantic_value_hints(source, schema, policy, version)
    try:
        plan, query_ir = generate_semantic_plan_ir(
            payload.question,
            version,
            api_key=api_key,
            model=model.model_name,
            base_url=model.base_url,
            temperature=float(config.get("temperature", 0.1)),
            timeout=float(config.get("timeout", 60)),
            max_retries=int(config.get("retry", config.get("max_retries", 2))),
            value_hints=value_hints,
        )
    except StructuredDataError as exc:
        _raise_structured(exc)
    report = validate_ir(query_ir, plan, version)
    if not report["ok"]:
        raise HTTPException(422, {"code": "SEMANTIC_QUERY_INVALID", "message": "生成的查询未通过确定性校验", "validation": report})
    try:
        compiled = compile_structured_query(source, version, schema, query_ir, max_rows=payload.max_rows)
    except StructuredDataError as exc:
        _raise_structured(exc)
    compiled_payload = _compile_payload(compiled, report)
    if not user.is_admin:
        compiled_payload.pop("sql_template", None)
        compiled_payload.pop("referenced_objects", None)
        compiled_payload.pop("referenced_columns", None)
    response: dict[str, Any] = {
        "question": payload.question,
        "plan": plan.model_dump(),
        "query_ir": query_ir.model_dump(),
        "compiled": compiled_payload,
        "result": None,
    }
    if payload.execute:
        response["result"] = execute_structured_query_api(
            payload=StructuredExecuteRequest(
                mapping_version_id=version.id,
                plan=plan,
                query_ir=query_ir,
                max_rows=payload.max_rows,
            ),
            user=user,
            db=db,
        )
    return response


@router.post("/structured-query/execute")
def execute_structured_query_api(
    payload: StructuredExecuteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, version, source, schema = _query_context(db, payload.mapping_version_id, user)
    report = validate_ir(payload.query_ir, payload.plan, version)
    if not report["ok"]:
        raise HTTPException(422, {"code": "QUERY_IR_INVALID", "message": "查询计划或 IR 未通过校验", "validation": report})
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.user_id != user.id or conversation.tenant_id != user.tenant_id or conversation.status == "deleted":
            raise HTTPException(404, "会话不存在")
    if payload.message_id:
        message = db.get(ConversationMessage, payload.message_id)
        if message is None or message.user_id != user.id or message.tenant_id != user.tenant_id:
            raise HTTPException(404, "消息不存在")
    try:
        compiled = compile_structured_query(source, version, schema, payload.query_ir, max_rows=payload.max_rows)
    except StructuredDataError as exc:
        _raise_structured(exc)
    run = StructuredQueryRun(
        tenant_id=user.tenant_id,
        space_id=source.space_id,
        source_id=source.id,
        user_id=user.id,
        mapping_version_id=version.id,
        schema_version_id=schema.id,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        original_question=payload.plan.original_question,
        semantic_plan=payload.plan.model_dump(),
        plan_fingerprint=report["plan_fingerprint"],
        query_ir=payload.query_ir.model_dump(),
        ir_fingerprint=report["ir_fingerprint"],
        dialect=compiled.dialect,
        sql_template=compiled.sql_template,
        parameter_summary=compiled.parameter_summary,
        referenced_objects=compiled.referenced_objects,
        referenced_columns=compiled.referenced_columns,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    policy = get_or_create_preview_policy(db, source)
    try:
        result = execute_compiled_query(
            source,
            compiled,
            timeout_seconds=max(1, min(policy.query_timeout_seconds, 120)),
            max_result_bytes=max(policy.max_result_bytes, 10_000),
            run_id=run.id,
        )
    except StructuredDataError as exc:
        run = db.get(StructuredQueryRun, run.id)
        run.status = "cancelled" if run.cancel_requested_at else "failed"
        run.error_code = "QUERY_CANCELLED" if run.cancel_requested_at else exc.code
        run.error_message = "查询已取消" if run.cancel_requested_at else str(exc)
        run.finished_at = datetime.now(timezone.utc)
        audit(db, user.tenant_id, user.id, "structured_query.execute", "structured_query_run", run.id, {"status": run.status, "error_code": run.error_code})
        db.commit()
        _raise_structured(exc)
    run = db.get(StructuredQueryRun, run.id)
    if run.cancel_requested_at:
        run.status = "cancelled"
        run.error_code = "QUERY_CANCELLED"
        run.error_message = "查询已取消"
        run.result_rows = []
        run.result_columns = []
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(409, {"code": "QUERY_CANCELLED", "message": "查询已取消"})
    run.status = "succeeded"
    run.result_columns = result["columns"]
    run.result_rows = result["rows"]
    run.row_count = result["row_count"]
    run.result_bytes = result["result_bytes"]
    run.truncated = result["truncated"]
    run.elapsed_ms = result["elapsed_ms"]
    run.warnings = result["warnings"]
    run.finished_at = datetime.now(timezone.utc)
    source_label = source.name
    citation = StructuredQueryCitation(
        tenant_id=user.tenant_id,
        query_run_id=run.id,
        message_id=payload.message_id,
        citation_number=1,
        label=f"{source_label} · {payload.plan.intent[:200]}",
        summary={
            "source_id": source.id,
            "source_name": source_label,
            "database_type": compiled.dialect,
            "mapping_version_id": version.id,
            "schema_version_id": schema.id,
            "query_time": result["query_time"],
            "row_count": result["row_count"],
            "truncated": result["truncated"],
            "query_run_id": run.id,
        },
    )
    db.add(citation)
    audit(db, user.tenant_id, user.id, "structured_query.execute", "structured_query_run", run.id, {"status": "succeeded", "row_count": run.row_count, "truncated": run.truncated, "elapsed_ms": run.elapsed_ms})
    db.commit()
    return {
        "query_run_id": run.id,
        **result,
        "validation": {
            "plan_fingerprint": report["plan_fingerprint"],
            "ir_fingerprint": report["ir_fingerprint"],
        },
        "safe_query_summary": {
            "intent": payload.plan.intent,
            "dialect": compiled.dialect,
            "mapping_version_id": version.id,
            "schema_version_id": schema.id,
            "query_fingerprint": compiled.query_fingerprint,
        },
        "source_citations": [serialize_row(citation)],
    }


@router.post("/structured-query/{run_id}/cancel")
def cancel_structured_query_api(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(StructuredQueryRun, run_id)
    if run is None or run.deleted_at is not None or run.tenant_id != user.tenant_id or run.user_id != user.id:
        raise HTTPException(404, "查询任务不存在")
    require_space_permission(db, user, run.space_id, "read")
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "该查询已经结束")
    run.cancel_requested_at = datetime.now(timezone.utc)
    interrupted = cancel_active_query(run.id)
    audit(db, user.tenant_id, user.id, "structured_query.cancel", "structured_query_run", run.id, {"active_connection_interrupted": interrupted})
    db.commit()
    return {"ok": True, "status": "cancelling", "active_connection_interrupted": interrupted}


def _run_payload(row: StructuredQueryRun, *, physical_details: bool) -> dict[str, Any]:
    value = serialize_row(row)
    if not physical_details:
        value.pop("sql_template", None)
        value.pop("referenced_objects", None)
        value.pop("referenced_columns", None)
    return value


@router.get("/structured-query/runs")
def list_structured_query_runs(
    source_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(StructuredQueryRun).where(
        StructuredQueryRun.tenant_id == user.tenant_id,
        StructuredQueryRun.user_id == user.id,
        StructuredQueryRun.deleted_at.is_(None),
    ).order_by(StructuredQueryRun.created_at.desc()).limit(limit)
    if source_id:
        source = _source(db, source_id, user)
        query = query.where(StructuredQueryRun.source_id == source.id)
    return {"items": [_run_payload(row, physical_details=user.is_admin) for row in db.scalars(query)]}


@router.get("/structured-query/runs/{run_id}")
def get_structured_query_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(StructuredQueryRun, run_id)
    if run is None or run.deleted_at is not None or run.tenant_id != user.tenant_id or run.user_id != user.id:
        raise HTTPException(404, "查询任务不存在")
    require_space_permission(db, user, run.space_id, "read")
    value = _run_payload(run, physical_details=user.is_admin)
    value["citations"] = [
        serialize_row(row) for row in db.scalars(select(StructuredQueryCitation).where(
            StructuredQueryCitation.query_run_id == run.id,
            StructuredQueryCitation.deleted_at.is_(None),
        ).order_by(StructuredQueryCitation.citation_number))
    ]
    return value
