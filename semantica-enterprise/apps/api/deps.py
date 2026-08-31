from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.platform.database import get_db
from packages.platform.models import KnowledgeSpace, OrgUnit, SpaceGrant, User, UserRole
from packages.platform.security import decode_access_token


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
