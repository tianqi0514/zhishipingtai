from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.structured_schemas import SemanticMappingManifest
from packages.platform.database import Base
from packages.platform.models import (
    DataSourceSchemaVersion,
    KnowledgeSpace,
    Ontology,
    OntologyTerm,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
    Tenant,
    User,
)
from packages.platform.semantic_mapping import seal_manifest, validate_mapping_manifest


def _fixture() -> tuple[Session, SemanticMappingSet, SemanticMappingVersion]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(id="tenant", code="tenant", name="租户")
    space = KnowledgeSpace(id="space", tenant_id=tenant.id, code="space", name="空间")
    user = User(id="user", tenant_id=tenant.id, username="admin", password_hash="x", display_name="管理员", is_admin=True)
    source = SourceConnector(
        id="source", tenant_id=tenant.id, space_id=space.id, name="经营库",
        source_type="database", config={"dialect": "postgresql"},
    )
    ontology = Ontology(id="ontology", tenant_id=tenant.id, space_id=space.id, code="business", name="业务本体", namespace="urn:test")
    entity_term = OntologyTerm(id="term-company", ontology_id=ontology.id, code="company", label="企业", term_type="class")
    property_term = OntologyTerm(id="term-name", ontology_id=ontology.id, code="name", label="名称", term_type="property")
    schema = DataSourceSchemaVersion(
        id="schema", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        version_number=1, schema_fingerprint="a" * 64, status="current",
        catalog={"objects": [{
            "id": "public.companies", "schema": "public", "name": "companies",
            "primary_key": ["id"], "foreign_keys": [],
            "columns": [
                {"id": "public.companies.id", "name": "id", "type_family": "integer"},
                {"id": "public.companies.name", "name": "name", "type_family": "string"},
            ],
        }]},
    )
    mapping_set = SemanticMappingSet(
        id="mapping", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        ontology_id=ontology.id, name="企业映射",
    )
    manifest = SemanticMappingManifest.model_validate({
        "source_id": source.id,
        "ontology_id": ontology.id,
        "schema_version_id": schema.id,
        "entities": [{
            "id": "company", "ontology_term_id": entity_term.id, "label": "企业",
            "fragments": [{
                "id": "company-main", "object_id": "public.companies", "role": "primary",
                "identity_column_ids": ["public.companies.id"], "display_column_id": "public.companies.name",
            }],
        }],
        "attributes": [{
            "id": "company-name", "ontology_term_id": property_term.id, "entity_id": "company",
            "fragment_id": "company-main", "column_id": "public.companies.name", "label": "名称",
        }],
    }).model_dump()
    manifest, mapping_hash = seal_manifest(manifest)
    version = SemanticMappingVersion(
        id="version", tenant_id=tenant.id, space_id=space.id, source_id=source.id,
        mapping_set_id=mapping_set.id, schema_version_id=schema.id,
        schema_fingerprint=schema.schema_fingerprint, mapping_hash=mapping_hash,
        version_number=1, manifest=manifest, status="draft", created_by=user.id,
    )
    db.add_all([tenant, space, user, source, ontology, entity_term, property_term, schema, mapping_set, version])
    db.commit()
    return db, mapping_set, version


def test_mapping_validation_accepts_existing_ontology_and_schema() -> None:
    db, mapping_set, version = _fixture()
    try:
        report = validate_mapping_manifest(db, mapping_set, version)
        assert report["ok"] is True
        assert report["counts"] == {"entities": 1, "attributes": 1, "relationships": 0, "fragments": 1}
    finally:
        db.close()

def test_mapping_validation_rejects_unknown_physical_column() -> None:
    db, mapping_set, version = _fixture()
    try:
        version.manifest["attributes"][0]["column_id"] = "public.companies.password"
        report = validate_mapping_manifest(db, mapping_set, version)
        assert report["ok"] is False
        assert any("属性字段不存在" in item for item in report["errors"])
    finally:
        db.close()
