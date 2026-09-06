from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.deps import get_effective_space_permission
from apps.api.routes import get_space, list_spaces
from packages.platform.database import Base
from packages.platform.models import (
    KnowledgeSpace,
    OrgUnit,
    Role,
    SpaceGrant,
    Tenant,
    User,
    UserRole,
)


def _database() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _user(db: Session, tenant: Tenant, username: str, **values) -> User:
    row = User(
        tenant_id=tenant.id,
        username=username,
        display_name=username,
        password_hash="not-used",
        enabled=True,
        **values,
    )
    db.add(row)
    db.flush()
    return row


def test_effective_permission_distinguishes_admin_owner_and_grant_levels() -> None:
    with _database() as db:
        tenant = Tenant(code="space-permission", name="空间权限")
        db.add(tenant)
        db.flush()
        admin = _user(db, tenant, "admin", is_admin=True)
        owner = _user(db, tenant, "owner")
        manager = _user(db, tenant, "manager")
        writer = _user(db, tenant, "writer")
        reader = _user(db, tenant, "reader")
        outsider = _user(db, tenant, "outsider")
        space = KnowledgeSpace(
            tenant_id=tenant.id,
            code="effective-permission",
            name="有效权限",
            owner_id=owner.id,
        )
        db.add(space)
        db.flush()
        db.add_all(
            [
                SpaceGrant(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_type="user",
                    subject_id=manager.id,
                    permission="manage",
                    effect="allow",
                ),
                SpaceGrant(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_type="user",
                    subject_id=writer.id,
                    permission="write",
                    effect="allow",
                ),
                SpaceGrant(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_type="user",
                    subject_id=reader.id,
                    permission="read",
                    effect="allow",
                ),
            ]
        )
        db.commit()

        assert get_effective_space_permission(db, admin, space.id) == "admin"
        assert get_effective_space_permission(db, owner, space.id) == "owner"
        assert get_effective_space_permission(db, manager, space.id) == "manage"
        assert get_effective_space_permission(db, writer, space.id) == "write"
        assert get_effective_space_permission(db, reader, space.id) == "read"
        assert get_effective_space_permission(db, outsider, space.id) is None


def test_effective_permission_keeps_role_org_hierarchy_and_explicit_deny_semantics() -> None:
    with _database() as db:
        tenant = Tenant(code="space-subjects", name="授权主体")
        db.add(tenant)
        db.flush()
        parent = OrgUnit(tenant_id=tenant.id, code="group", name="集团")
        db.add(parent)
        db.flush()
        child = OrgUnit(tenant_id=tenant.id, code="subsidiary", name="子企业", parent_id=parent.id)
        role = Role(tenant_id=tenant.id, code="steward", name="知识专员", enabled=True)
        db.add_all([child, role])
        db.flush()
        org_writer = _user(db, tenant, "org-writer", org_unit_id=child.id)
        role_manager = _user(db, tenant, "role-manager")
        denied = _user(db, tenant, "denied")
        db.add_all(
            [
                UserRole(user_id=role_manager.id, role_id=role.id),
                UserRole(user_id=denied.id, role_id=role.id),
            ]
        )
        space = KnowledgeSpace(tenant_id=tenant.id, code="subject-space", name="主体空间")
        db.add(space)
        db.flush()
        db.add_all(
            [
                SpaceGrant(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_type="org",
                    subject_id=parent.id,
                    permission="write",
                    effect="allow",
                ),
                SpaceGrant(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_type="role",
                    subject_id=role.id,
                    permission="manage",
                    effect="allow",
                ),
                SpaceGrant(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_type="user",
                    subject_id=denied.id,
                    permission="read",
                    effect="deny",
                ),
            ]
        )
        db.commit()

        assert get_effective_space_permission(db, org_writer, space.id) == "write"
        assert get_effective_space_permission(db, role_manager, space.id) == "manage"
        assert get_effective_space_permission(db, denied, space.id) is None


def test_spaces_api_projects_effective_permission_and_detail_enforces_visibility() -> None:
    with _database() as db:
        tenant = Tenant(code="space-api", name="空间接口")
        db.add(tenant)
        db.flush()
        owner = _user(db, tenant, "owner")
        reader = _user(db, tenant, "reader")
        hidden_user = _user(db, tenant, "hidden")
        space = KnowledgeSpace(
            tenant_id=tenant.id,
            code="space-api-visible",
            name="可见空间",
            owner_id=owner.id,
        )
        db.add(space)
        db.flush()
        db.add(
            SpaceGrant(
                tenant_id=tenant.id,
                space_id=space.id,
                subject_type="user",
                subject_id=reader.id,
                permission="read",
                effect="allow",
            )
        )
        db.commit()

        rows = list_spaces(reader, db)
        assert len(rows) == 1
        assert rows[0]["id"] == space.id
        assert rows[0]["effective_permission"] == "read"
        assert get_space(space.id, reader, db)["effective_permission"] == "read"

        assert list_spaces(hidden_user, db) == []
        with pytest.raises(HTTPException) as exc_info:
            get_space(space.id, hidden_user, db)
        assert exc_info.value.status_code == 403
