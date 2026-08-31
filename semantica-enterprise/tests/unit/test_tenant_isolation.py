from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.routes import (
    _must_tenant,
    list_audit,
    list_models,
    list_orgs,
    list_policies,
    list_roles,
    list_spaces,
    list_users,
)
from packages.platform.database import Base
from packages.platform.models import (
    AuditEvent,
    KnowledgeSpace,
    ModelConfig,
    OrgUnit,
    ParserPolicy,
    Role,
    Tenant,
    User,
)


def _seed_two_tenants(db: Session) -> tuple[User, User]:
    first = Tenant(code="tenant-a", name="Tenant A")
    second = Tenant(code="tenant-b", name="Tenant B")
    db.add_all([first, second])
    db.flush()
    admins: list[User] = []
    for suffix, tenant in (("a", first), ("b", second)):
        admin = User(
            tenant_id=tenant.id,
            username=f"admin-{suffix}",
            password_hash="not-used",
            display_name=f"Admin {suffix}",
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        db.add_all(
            [
                OrgUnit(tenant_id=tenant.id, code=f"org-{suffix}", name=f"Org {suffix}"),
                Role(tenant_id=tenant.id, code=f"role-{suffix}", name=f"Role {suffix}"),
                ModelConfig(
                    tenant_id=tenant.id,
                    name=f"Model {suffix}",
                    model_kind="llm",
                    provider="openai-compatible",
                    model_name="test",
                ),
                ParserPolicy(tenant_id=tenant.id, name=f"Parser {suffix}"),
                KnowledgeSpace(
                    tenant_id=tenant.id,
                    code=f"space-{suffix}",
                    name=f"Space {suffix}",
                    owner_id=admin.id,
                ),
                AuditEvent(
                    tenant_id=tenant.id,
                    actor_id=admin.id,
                    action=f"test.{suffix}",
                    object_type="test",
                ),
            ]
        )
        admins.append(admin)
    db.commit()
    return admins[0], admins[1]


def test_tenant_owned_lists_never_return_other_tenant_rows() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first, _ = _seed_two_tenants(db)

        assert {row["tenant_id"] for row in list_orgs(first, db)} == {first.tenant_id}
        assert {row["tenant_id"] for row in list_roles(first, db)} == {first.tenant_id}
        assert {row["tenant_id"] for row in list_users(first, db)} == {first.tenant_id}
        assert {row["tenant_id"] for row in list_models(first, db)} == {first.tenant_id}
        assert {row["tenant_id"] for row in list_policies(first, db)} == {first.tenant_id}
        assert {row["tenant_id"] for row in list_spaces(first, db)} == {first.tenant_id}
        assert {row["tenant_id"] for row in list_audit(first, db)} == {first.tenant_id}


def test_cross_tenant_identifier_is_reported_as_not_found() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first, second = _seed_two_tenants(db)
        other_role = db.query(Role).filter(Role.tenant_id == second.tenant_id).one()

        with pytest.raises(HTTPException) as exc_info:
            _must_tenant(db, Role, other_role.id, first.tenant_id, "角色")
        assert exc_info.value.status_code == 404
