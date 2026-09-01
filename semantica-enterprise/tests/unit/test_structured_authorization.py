from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.structured_data import _mapping_set, _source
from packages.platform.database import Base
from packages.platform.models import (
    KnowledgeSpace,
    SemanticMappingSet,
    SourceConnector,
    Tenant,
    User,
)


def test_structured_sources_and_mappings_do_not_cross_tenant_or_space_boundaries() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant_a = Tenant(code="tenant-a", name="Tenant A")
        tenant_b = Tenant(code="tenant-b", name="Tenant B")
        db.add_all([tenant_a, tenant_b])
        db.flush()
        owner = User(
            tenant_id=tenant_a.id, username="owner", password_hash="x",
            display_name="Owner", is_admin=True,
        )
        no_grant = User(
            tenant_id=tenant_a.id, username="no-grant", password_hash="x",
            display_name="No grant", is_admin=False,
        )
        other_tenant = User(
            tenant_id=tenant_b.id, username="other", password_hash="x",
            display_name="Other", is_admin=True,
        )
        db.add_all([owner, no_grant, other_tenant])
        db.flush()
        space = KnowledgeSpace(
            tenant_id=tenant_a.id, code="space-a", name="Space A", owner_id=owner.id,
        )
        db.add(space)
        db.flush()
        source = SourceConnector(
            tenant_id=tenant_a.id, space_id=space.id, name="Database",
            source_type="database", config={"dialect": "postgresql"},
        )
        db.add(source)
        db.flush()
        mapping = SemanticMappingSet(
            tenant_id=tenant_a.id, space_id=space.id, source_id=source.id,
            ontology_id="ontology", name="Mapping",
        )
        db.add(mapping)
        db.commit()

        assert _source(db, source.id, owner).id == source.id
        assert _mapping_set(db, mapping.id, owner).id == mapping.id

        with pytest.raises(HTTPException) as no_source_grant:
            _source(db, source.id, no_grant)
        assert no_source_grant.value.status_code == 403
        with pytest.raises(HTTPException) as no_mapping_grant:
            _mapping_set(db, mapping.id, no_grant)
        assert no_mapping_grant.value.status_code == 403

        with pytest.raises(HTTPException) as cross_source:
            _source(db, source.id, other_tenant)
        assert cross_source.value.status_code == 404
        with pytest.raises(HTTPException) as cross_mapping:
            _mapping_set(db, mapping.id, other_tenant)
        assert cross_mapping.value.status_code == 404
