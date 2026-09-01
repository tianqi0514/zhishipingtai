from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.platform.database import Base
from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    ContentElement,
    DataSourceSchemaVersion,
    Document,
    DocumentVersion,
    Fact,
    KnowledgeSpace,
    Ontology,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
    Tenant,
    User,
)
from packages.platform.structured_materialization import materialize_database_mapping


def _fixture() -> tuple[Session, Document, DocumentVersion]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(id="tenant", code="tenant", name="租户")
    user = User(
        id="user", tenant_id=tenant.id, username="admin", password_hash="x",
        display_name="管理员", is_admin=True,
    )
    space = KnowledgeSpace(id="space", tenant_id=tenant.id, code="space", name="空间")
    source = SourceConnector(
        id="source", tenant_id=tenant.id, space_id=space.id, name="经营库",
        source_type="database",
        config={"dialect": "postgresql", "graph_materialization_enabled": True},
    )
    ontology = Ontology(
        id="ontology", tenant_id=tenant.id, space_id=space.id, code="business",
        name="业务本体", namespace="urn:test",
    )
    schema = DataSourceSchemaVersion(
        id="schema", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        version_number=1, schema_fingerprint="a" * 64, status="current",
        catalog={"objects": [{
            "id": "public.customers", "schema": "public", "name": "customers",
            "primary_key": ["id"], "foreign_keys": [],
            "columns": [
                {"id": "public.customers.id", "name": "id", "type_family": "integer"},
                {"id": "public.customers.name", "name": "name", "type_family": "string"},
            ],
        }]},
    )
    mapping_set = SemanticMappingSet(
        id="mapping", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        ontology_id=ontology.id, name="客户映射", status="active", active_version_id="mapping-v1",
    )
    mapping = SemanticMappingVersion(
        id="mapping-v1", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        mapping_set_id=mapping_set.id, schema_version_id=schema.id,
        schema_fingerprint=schema.schema_fingerprint, mapping_hash="b" * 64,
        version_number=1, status="active", created_by=user.id,
        manifest={
            "entities": [{
                "id": "customer", "label": "客户",
                "fragments": [{
                    "id": "customer-main", "object_id": "public.customers", "role": "primary",
                    "identity_column_ids": ["public.customers.id"],
                    "display_column_id": "public.customers.name",
                }],
            }],
            "attributes": [{
                "id": "customer-name", "entity_id": "customer", "fragment_id": "customer-main",
                "column_id": "public.customers.name", "label": "名称",
            }],
            "relationships": [],
        },
    )
    document = Document(
        id="document", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        title="经营库快照", owner_id=user.id, status="processing",
    )
    version = DocumentVersion(
        id="version-1", tenant_id=tenant.id, document_id=document.id, version_number=1,
        filename="snapshot.json", content_type="application/json", size=10,
        sha256="c" * 64, object_key="fixture/snapshot.json", status="processing",
    )
    policy = ChunkPolicy(
        id="chunk-policy", tenant_id=tenant.id, name="默认", chunk_size=800, chunk_overlap=100,
    )
    element = ContentElement(
        id="element", tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, element_id="stable-row", element_type="record", ordinal=1,
        text="id: 1\nname: 国联客户", structural_path="public.customers[1]",
        element_metadata={
            "source_id": source.id, "object_id": "public.customers",
            "row_key": "row-one", "row": {"id": 1, "name": "国联客户"},
        },
    )
    chunk = Chunk(
        id="chunk", tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, element_id=element.id, chunk_policy_id=policy.id,
        chunk_id="chunk-one", ordinal=1, text=element.text, content_hash="d" * 64,
        structural_path=element.structural_path, status="staged",
    )
    db.add_all([
        tenant, user, space, source, ontology, schema, mapping_set, mapping,
        document, version, policy, element, chunk,
    ])
    db.commit()
    return db, document, version


def test_database_mapping_materializes_deterministic_entity_and_fact() -> None:
    db, document, version = _fixture()
    try:
        result = materialize_database_mapping(db, document=document, version=version)
        db.commit()
        assert result == {
            "enabled": True,
            "mapping_version_id": "mapping-v1",
            "schema_version_id": "schema",
            "entities": 1,
            "attribute_facts": 1,
            "relationship_facts": 0,
        }
        entity = db.scalar(select(CanonicalEntity))
        assert entity is not None
        assert entity.canonical_name == "国联客户"
        assert entity.normalized_name == "db:source:customer:row-one"
        assert entity.properties["mapping_version_id"] == "mapping-v1"
        fact = db.scalar(select(Fact))
        assert fact is not None
        assert fact.predicate == "名称"
        assert fact.object_value == "国联客户"
        assert fact.source_chunk_id == "chunk"
    finally:
        db.close()


def test_new_snapshot_retires_rows_deleted_from_database() -> None:
    db, document, version = _fixture()
    try:
        materialize_database_mapping(db, document=document, version=version)
        db.commit()
        second = DocumentVersion(
            id="version-2", tenant_id="tenant", document_id=document.id, version_number=2,
            filename="snapshot.json", content_type="application/json", size=2,
            sha256="e" * 64, object_key="fixture/snapshot-2.json", status="processing",
        )
        db.add(second)
        db.commit()
        result = materialize_database_mapping(db, document=document, version=second)
        db.commit()
        assert result["entities"] == 0
        entity = db.scalar(select(CanonicalEntity))
        assert entity is not None
        assert entity.status == "superseded"
    finally:
        db.close()
