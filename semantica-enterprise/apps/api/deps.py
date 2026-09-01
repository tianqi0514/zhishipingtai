from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.platform.database import get_db
from packages.platform.models import (
    Application,
    ApplicationCredential,
    KnowledgeSpace,
    OrgUnit,
    Role,
    SpaceGrant,
    User,
    UserRole,
)
from packages.platform.security import decode_access_token, decode_application_access_token


@dataclass(frozen=True)
class ApplicationPrincipal:
    application: Application
    credential: ApplicationCredential
    scopes: frozenset[str]
    jti: str

    @property
    def tenant_id(self) -> str:
        return self.application.tenant_id


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    user = db.get(User, payload.get("sub"))
    if (
        user is None
        or not user.enabled
        or user.deleted_at is not None
        or payload.get("tenant_id") != user.tenant_id
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def get_user_permissions(db: Session, user: User) -> list[str]:
    """Return the effective platform permissions granted by enabled roles.

    Space access remains governed by ``SpaceGrant``.  These permissions only
    control platform-wide workbenches such as application delivery and audit.
    Keeping the two scopes separate prevents an application developer role
    from implicitly gaining access to every knowledge space.
    """
    if user.is_admin:
        return ["*"]
    role_ids = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id)))
    if not role_ids:
        return []
    permissions: set[str] = set()
    roles = db.scalars(
        select(Role).where(
            Role.id.in_(role_ids),
            Role.tenant_id == user.tenant_id,
            Role.enabled.is_(True),
            Role.deleted_at.is_(None),
        )
    )
    for role in roles:
        permissions.update(str(item).strip() for item in (role.permissions or []) if str(item).strip())
    return sorted(permissions)


def permission_matches(granted: str, required: str) -> bool:
    return granted == "*" or granted == required or (
        granted.endswith(".*") and required.startswith(granted[:-1])
    )


def has_platform_permission(db: Session, user: User, required: str) -> bool:
    return any(permission_matches(item, required) for item in get_user_permissions(db, user))


def require_permission(permission: str) -> Callable:
    def dependency(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not has_platform_permission(db, user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少平台权限：{permission}",
            )
        return user

    return dependency


def get_current_application(request: Request, db: Session = Depends(get_db)) -> ApplicationPrincipal:
    """Resolve a short-lived application token and re-check its live credential state."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少应用访问令牌")
    try:
        payload = decode_application_access_token(authorization[7:].strip())
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="应用访问令牌无效或已过期")
    application = db.get(Application, payload.get("application_id"))
    credential = db.get(ApplicationCredential, payload.get("credential_id"))
    now = datetime.now(timezone.utc)
    if (
        application is None
        or application.deleted_at is not None
        or not application.enabled
        or application.status != "active"
        or application.id != payload.get("sub")
        or application.tenant_id != payload.get("tenant_id")
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="应用已停用或不可用")
    if (
        credential is None
        or credential.deleted_at is not None
        or credential.application_id != application.id
        or credential.tenant_id != application.tenant_id
        or credential.client_id != payload.get("client_id")
        or credential.revoked_at is not None
        or (_aware(credential.expires_at) is not None and _aware(credential.expires_at) <= now)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="应用凭据已撤销或过期")
    token_scopes = frozenset(str(payload.get("scope") or "").split())
    credential_scopes = frozenset(credential.scopes or [])
    if not token_scopes.issubset(credential_scopes):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="应用令牌权限已失效")
    return ApplicationPrincipal(
        application=application,
        credential=credential,
        scopes=token_scopes,
        jti=str(payload["jti"]),
    )


def require_application_scope(scope: str) -> Callable:
    def dependency(principal: ApplicationPrincipal = Depends(get_current_application)) -> ApplicationPrincipal:
        if scope not in principal.scopes and "*" not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"应用缺少权限：{scope}")
        return principal

    return dependency


_PERMISSION_RANK = {"read": 1, "write": 2, "manage": 3}


def has_space_permission(db: Session, user: User, space_id: str, required: str) -> bool:
    space = db.get(KnowledgeSpace, space_id)
    if space is None or space.deleted_at is not None or space.tenant_id != user.tenant_id:
        return False
    if user.is_admin:
        return True
    if space and space.owner_id == user.id:
        return True
    role_ids = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id)))
    candidates = [("user", user.id)]
    if user.org_unit_id:
        current_id = user.org_unit_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            unit = db.get(OrgUnit, current_id)
            if unit is None or unit.deleted_at is not None or unit.tenant_id != user.tenant_id:
                break
            candidates.append(("org", current_id))
            current_id = unit.parent_id
    candidates.extend(("role", role_id) for role_id in role_ids)
    grants = list(
        db.scalars(
            select(SpaceGrant).where(
                SpaceGrant.tenant_id == user.tenant_id,
                SpaceGrant.space_id == space_id,
                SpaceGrant.deleted_at.is_(None),
            )
        )
    )
    matched = [
        grant
        for grant in grants
        if (grant.subject_type, grant.subject_id) in candidates
        and _PERMISSION_RANK.get(grant.permission, 0) >= _PERMISSION_RANK[required]
    ]
    if any(grant.effect == "deny" for grant in matched):
        return False
    return any(grant.effect == "allow" for grant in matched)


def require_space_permission(db: Session, user: User, space_id: str, required: str) -> None:
    if not has_space_permission(db, user, space_id, required):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无该知识空间权限")
