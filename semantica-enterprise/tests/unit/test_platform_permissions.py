from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.deps import get_user_permissions, has_platform_permission, permission_matches
from packages.platform.database import Base
from packages.platform.models import Role, Tenant, User, UserRole
from packages.platform.security import hash_password


def test_permission_matching_supports_exact_and_namespace_wildcards() -> None:
    assert permission_matches("application.manage", "application.manage")
    assert permission_matches("document.*", "document.read")
    assert permission_matches("*", "audit.read")
    assert not permission_matches("document.read", "document.create")
    assert not permission_matches("document.*", "source.read")


def test_effective_permissions_only_include_enabled_roles_in_same_tenant() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="permission-test", name="权限测试")
        other_tenant = Tenant(code="permission-other", name="其他租户")
        db.add_all([tenant, other_tenant])
        db.flush()
        user = User(
            tenant_id=tenant.id,
            username="builder",
            password_hash=hash_password("Builder@12345"),
            display_name="应用开发者",
            enabled=True,
        )
        active = Role(
            tenant_id=tenant.id,
            code="builder",
            name="应用开发者",
            permissions=["application.manage", "audit.read"],
            enabled=True,
        )
        disabled = Role(
            tenant_id=tenant.id,
            code="disabled",
            name="停用角色",
            permissions=["system.manage"],
            enabled=False,
        )
        foreign = Role(
            tenant_id=other_tenant.id,
            code="foreign",
            name="其他租户角色",
            permissions=["*"],
            enabled=True,
        )
        db.add_all([user, active, disabled, foreign])
        db.flush()
        db.add_all([
            UserRole(user_id=user.id, role_id=active.id),
            UserRole(user_id=user.id, role_id=disabled.id),
            UserRole(user_id=user.id, role_id=foreign.id),
        ])
        db.commit()

        assert get_user_permissions(db, user) == ["application.manage", "audit.read"]
        assert has_platform_permission(db, user, "application.manage")
        assert not has_platform_permission(db, user, "system.manage")
