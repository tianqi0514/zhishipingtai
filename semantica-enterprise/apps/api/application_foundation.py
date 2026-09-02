from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.application_schemas import (
    ApplicationCreate,
    ApplicationUpdate,
    CredentialCreate,
    CredentialTokenRequest,
    GrantCreate,
    KnowledgeProductCreate,
    KnowledgeProductUpdate,
    ProductAliasMove,
    ProductReleaseCreate,
    ScenarioCreate,
    ScenarioUpdate,
    ScenarioInvokeRequest,
    ScenarioVersionCreate,
)
from apps.api.deps import (
    ApplicationPrincipal,
    get_current_application,
    get_current_user,
    has_space_permission,
    require_permission,
)
from apps.api.utils import apply_patch, serialize_row
from packages.platform.audit import audit
from packages.platform.config import get_settings
from packages.platform.database import get_db
from packages.platform.models import (
    AnalysisRuleSet,
    Application,
    ApplicationCredential,
    ApplicationGrant,
    ApplicationInvocation,
    ApplicationScenario,
    ApplicationScenarioVersion,
    KnowledgeProduct,
    KnowledgeProductAlias,
    KnowledgeProductAliasHistory,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
    KnowledgeProductSpace,
    KnowledgeRelease,
    KnowledgeSpace,
    ModelConfig,
    OrgUnit,
    User,
)
from packages.platform.security import (
    create_application_access_token,
    hash_service_secret,
    verify_service_secret,
)
from packages.platform.application_services import (
    ApplicationConfigurationError,
    application_has_grant,
    resolve_scenario_product_release,
)
from packages.platform.knowledge_search import execute_hybrid_search


router = APIRouter(tags=["application-foundation"])
# Application builders are ordinary business users with an explicit platform
# role.  Keep the existing dependency call sites while replacing the previous
# hard-coded ``is_admin`` gate in this module only.
require_admin = require_permission("application.manage")


def _active(model: type) -> Any:
    return model.deleted_at.is_(None)


def _must_tenant(db: Session, model: type, row_id: str, tenant_id: str, label: str):
    row = db.get(model, row_id)
    if row is None or row.deleted_at is not None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return row


def _must_owned(db: Session, model: type, row_id: str, user: User, label: str):
    row = _must_tenant(db, model, row_id, user.tenant_id, label)
    if not user.is_admin and getattr(row, "owner_id", None) != user.id:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return row


def _commit(db: Session, conflict_message: str = "编码或名称已存在") -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict_message)


def _validate_owner(db: Session, tenant_id: str, owner_id: str) -> User:
    owner = _must_tenant(db, User, owner_id, tenant_id, "负责人")
    if not owner.enabled:
        raise HTTPException(status_code=400, detail="负责人账号已停用")
    return owner


def _validate_org(db: Session, tenant_id: str, org_unit_id: str | None) -> None:
    if org_unit_id:
        _must_tenant(db, OrgUnit, org_unit_id, tenant_id, "所属组织")


def _credential_view(row: ApplicationCredential) -> dict[str, Any]:
    return {
        "id": row.id,
        "application_id": row.application_id,
        "name": row.name,
        "client_id": row.client_id,
        "secret_prefix": row.secret_prefix,
        "scopes": row.scopes or [],
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "rotated_from_id": row.rotated_from_id,
        "revoked_at": row.revoked_at,
        "status": "revoked" if row.revoked_at else "active",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _new_credential_values() -> tuple[str, str]:
    return f"csa_{secrets.token_urlsafe(18)}", f"css_{secrets.token_urlsafe(42)}"


def _canonical_checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


# ---- Applications and service credentials -------------------------------------------


@router.get("/applications")
def list_applications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Application).where(Application.tenant_id == user.tenant_id, _active(Application))
    if not user.is_admin:
        query = query.where(Application.owner_id == user.id)
    return [serialize_row(row) for row in db.scalars(query.order_by(Application.updated_at.desc()))]


@router.post("/applications")
def create_application(
    payload: ApplicationCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    owner_id = payload.owner_id or admin.id
    if not admin.is_admin and owner_id != admin.id:
        raise HTTPException(status_code=403, detail="只能将应用负责人设置为当前用户")
    _validate_owner(db, admin.tenant_id, owner_id)
    _validate_org(db, admin.tenant_id, payload.org_unit_id)
    values = payload.model_dump(exclude={"owner_id"})
    recycled = db.scalar(
        select(Application).where(
            Application.tenant_id == admin.tenant_id,
            Application.code == payload.code,
        )
    )
    if recycled is not None:
        if recycled.deleted_at is None:
            raise HTTPException(status_code=409, detail="应用编码已存在")
        if not admin.is_admin and recycled.owner_id != admin.id:
            raise HTTPException(status_code=409, detail="应用编码被已删除应用占用，请联系管理员恢复")
        recycled.owner_id = owner_id
        recycled.deleted_at = None
        apply_patch(
            recycled,
            values,
            {"name", "description", "app_type", "environment", "org_unit_id", "status", "config", "enabled"},
        )
        # Credentials were revoked when the application was deleted.  Grants
        # are also retired so restoring a code never silently restores access
        # to a knowledge product or scenario.
        restored_at = datetime.now(timezone.utc)
        db.query(ApplicationGrant).filter(
            ApplicationGrant.application_id == recycled.id,
            ApplicationGrant.deleted_at.is_(None),
        ).update({"deleted_at": restored_at})
        audit(
            db,
            admin.tenant_id,
            admin.id,
            "application.restore",
            "application",
            recycled.id,
            {"code": recycled.code},
        )
        db.commit()
        db.refresh(recycled)
        return serialize_row(recycled)
    row = Application(tenant_id=admin.tenant_id, owner_id=owner_id, **values)
    db.add(row)
    audit(db, admin.tenant_id, admin.id, "application.create", "application", row.id, {"code": row.code})
    _commit(db, "应用编码已存在")
    db.refresh(row)
    return serialize_row(row)


@router.get("/applications/{row_id}")
def get_application(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, Application, row_id, user.tenant_id, "应用")
    if not user.is_admin and row.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权查看该应用")
    data = serialize_row(row)
    data["credential_count"] = db.scalar(
        select(func.count()).select_from(ApplicationCredential).where(
            ApplicationCredential.application_id == row.id,
            _active(ApplicationCredential),
        )
    )
    data["grant_count"] = db.scalar(
        select(func.count()).select_from(ApplicationGrant).where(
            ApplicationGrant.application_id == row.id,
            _active(ApplicationGrant),
        )
    )
    return data


@router.put("/applications/{row_id}")
def update_application(
    row_id: str,
    payload: ApplicationUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_owned(db, Application, row_id, admin, "应用")
    values = payload.model_dump(exclude_unset=True)
    if values.get("owner_id"):
        if not admin.is_admin and values["owner_id"] != admin.id:
            raise HTTPException(status_code=403, detail="不能将应用转交给其他用户")
        _validate_owner(db, admin.tenant_id, values["owner_id"])
    if "org_unit_id" in values:
        _validate_org(db, admin.tenant_id, values["org_unit_id"])
    apply_patch(
        row,
        values,
        {"name", "description", "app_type", "environment", "owner_id", "org_unit_id", "status", "config", "enabled"},
    )
    audit(db, admin.tenant_id, admin.id, "application.update", "application", row.id, values)
    db.commit()
    return serialize_row(row)


@router.delete("/applications/{row_id}")
def delete_application(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_owned(db, Application, row_id, admin, "应用")
    now = datetime.now(timezone.utc)
    row.deleted_at = now
    row.enabled = False
    row.status = "retired"
    db.query(ApplicationCredential).filter(
        ApplicationCredential.application_id == row.id,
        ApplicationCredential.revoked_at.is_(None),
        ApplicationCredential.deleted_at.is_(None),
    ).update({"revoked_at": now})
    db.query(ApplicationGrant).filter(
        ApplicationGrant.application_id == row.id,
        ApplicationGrant.deleted_at.is_(None),
    ).update({"deleted_at": now})
    audit(db, admin.tenant_id, admin.id, "application.delete", "application", row.id)
    db.commit()
    return {"ok": True}


@router.get("/applications/{application_id}/credentials")
def list_credentials(
    application_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _must_owned(db, Application, application_id, admin, "应用")
    rows = db.scalars(
        select(ApplicationCredential).where(
            ApplicationCredential.application_id == application_id,
            ApplicationCredential.tenant_id == admin.tenant_id,
            _active(ApplicationCredential),
        ).order_by(ApplicationCredential.created_at.desc())
    )
    return [_credential_view(row) for row in rows]


def _issue_credential(
    db: Session,
    *,
    application: Application,
    payload: CredentialCreate,
    rotated_from_id: str | None = None,
) -> tuple[ApplicationCredential, str]:
    client_id, client_secret = _new_credential_values()
    row = ApplicationCredential(
        application_id=application.id,
        tenant_id=application.tenant_id,
        name=payload.name,
        client_id=client_id,
        secret_prefix=client_secret[:12],
        secret_hash=hash_service_secret(client_secret),
        scopes=payload.scopes,
        expires_at=payload.expires_at,
        rotated_from_id=rotated_from_id,
    )
    db.add(row)
    return row, client_secret


@router.post("/applications/{application_id}/credentials")
def create_credential(
    application_id: str,
    payload: CredentialCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    application = _must_owned(db, Application, application_id, admin, "应用")
    if _aware_datetime(payload.expires_at) and _aware_datetime(payload.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="凭据过期时间必须晚于当前时间")
    row, secret = _issue_credential(db, application=application, payload=payload)
    audit(db, admin.tenant_id, admin.id, "application.credential.create", "application_credential", row.id)
    _commit(db, "凭据生成冲突，请重试")
    result = _credential_view(row)
    result["client_secret"] = secret
    result["secret_notice"] = "密钥仅显示一次，请立即安全保存"
    return result


@router.post("/applications/{application_id}/credentials/{credential_id}/rotate")
def rotate_credential(
    application_id: str,
    credential_id: str,
    payload: CredentialCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    application = _must_owned(db, Application, application_id, admin, "应用")
    old = _must_tenant(db, ApplicationCredential, credential_id, admin.tenant_id, "应用凭据")
    if old.application_id != application.id or old.revoked_at is not None:
        raise HTTPException(status_code=409, detail="应用凭据不可轮换")
    row, secret = _issue_credential(db, application=application, payload=payload, rotated_from_id=old.id)
    old.revoked_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "application.credential.rotate", "application_credential", old.id, {"new_id": row.id})
    _commit(db, "凭据生成冲突，请重试")
    result = _credential_view(row)
    result["client_secret"] = secret
    result["secret_notice"] = "原密钥已立即失效；新密钥仅显示一次"
    return result


@router.delete("/applications/{application_id}/credentials/{credential_id}")
def revoke_credential(
    application_id: str,
    credential_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _must_owned(db, Application, application_id, admin, "应用")
    row = _must_tenant(db, ApplicationCredential, credential_id, admin.tenant_id, "应用凭据")
    if row.application_id != application_id:
        raise HTTPException(status_code=404, detail="应用凭据不存在")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        audit(db, admin.tenant_id, admin.id, "application.credential.revoke", "application_credential", row.id)
        db.commit()
    return {"ok": True, "revoked_at": row.revoked_at}


@router.post("/application-auth/token")
def exchange_application_token(payload: CredentialTokenRequest, db: Session = Depends(get_db)):
    credential = db.scalar(
        select(ApplicationCredential).where(
            ApplicationCredential.client_id == payload.client_id,
            _active(ApplicationCredential),
        )
    )
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="应用凭据无效")
    now = datetime.now(timezone.utc)
    if credential is None or credential.revoked_at is not None or not verify_service_secret(payload.client_secret, credential.secret_hash):
        raise unauthorized
    expires_at = credential.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= now:
        raise unauthorized
    application = db.get(Application, credential.application_id)
    if (
        application is None
        or application.deleted_at is not None
        or not application.enabled
        or application.status != "active"
        or application.tenant_id != credential.tenant_id
    ):
        raise unauthorized
    requested = set(payload.scope.split()) if payload.scope else set(credential.scopes or [])
    if not requested or not requested.issubset(set(credential.scopes or [])):
        raise HTTPException(status_code=403, detail="请求的权限超出凭据授权范围")
    token, jti, token_expires = create_application_access_token(
        application_id=application.id,
        credential_id=credential.id,
        client_id=credential.client_id,
        tenant_id=credential.tenant_id,
        scopes=sorted(requested),
    )
    credential.last_used_at = now
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int((token_expires - now).total_seconds()),
        "scope": " ".join(sorted(requested)),
        "jti": jti,
    }


@router.get("/application-runtime/whoami")
def application_whoami(principal: ApplicationPrincipal = Depends(get_current_application)):
    return {
        "application_id": principal.application.id,
        "application_code": principal.application.code,
        "application_name": principal.application.name,
        "tenant_id": principal.tenant_id,
        "credential_id": principal.credential.id,
        "scopes": sorted(principal.scopes),
        "environment": principal.application.environment,
    }


@router.post("/application-runtime/scenarios/{scenario_code}/search")
def invoke_scenario_search(
    scenario_code: str,
    payload: ScenarioInvokeRequest,
    principal: ApplicationPrincipal = Depends(get_current_application),
    db: Session = Depends(get_db),
):
    if "scenario.invoke" not in principal.scopes and "*" not in principal.scopes:
        raise HTTPException(status_code=403, detail="应用缺少权限：scenario.invoke")
    scenario = db.scalar(select(ApplicationScenario).where(
        ApplicationScenario.tenant_id == principal.tenant_id,
        ApplicationScenario.code == scenario_code,
        ApplicationScenario.status == "active",
        ApplicationScenario.enabled.is_(True),
        _active(ApplicationScenario),
    ))
    if scenario is None or not scenario.current_version_id:
        raise HTTPException(status_code=404, detail="应用场景不存在或尚未发布")
    if not application_has_grant(
        db,
        application_id=principal.application.id,
        resource_type="scenario",
        resource_id=scenario.id,
    ):
        raise HTTPException(status_code=403, detail="应用未获得该场景的调用授权")
    version = _must_tenant(
        db,
        ApplicationScenarioVersion,
        scenario.current_version_id,
        principal.tenant_id,
        "场景版本",
    )
    if not application_has_grant(
        db,
        application_id=principal.application.id,
        resource_type="knowledge_product",
        resource_id=version.product_id,
        permission="read",
    ):
        raise HTTPException(status_code=403, detail="应用未获得场景所用知识产品的读取授权")
    try:
        product_release, space_ids = resolve_scenario_product_release(db, version)
    except ApplicationConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    request_id = uuid.uuid4().hex
    invocation = ApplicationInvocation(
        tenant_id=principal.tenant_id,
        application_id=principal.application.id,
        credential_id=principal.credential.id,
        scenario_id=scenario.id,
        scenario_version_id=version.id,
        product_release_id=product_release.id,
        operation="search",
        request_id=request_id,
        status="running",
        input_summary={"query_length": len(payload.query), "filter_keys": sorted(payload.filters)},
    )
    db.add(invocation)
    db.commit()
    started = time.perf_counter()
    policy = version.retrieval_policy or {}
    try:
        result = execute_hybrid_search(
            db,
            tenant_id=principal.tenant_id,
            user_id=principal.application.owner_id,
            query=payload.query,
            space_ids=space_ids,
            top_k=max(1, min(int(policy.get("top_k", 8)), 100)),
            use_keyword=bool(policy.get("use_keyword", True)),
            use_vector=bool(policy.get("use_vector", True)),
            use_graph=bool(policy.get("use_graph", True)),
            use_reranker=bool(policy.get("use_reranker", False)),
            filters=payload.filters,
            audit_action="application.scenario.search",
        )
        invocation.status = "succeeded"
        invocation.duration_ms = round((time.perf_counter() - started) * 1000)
        invocation.output_summary = {
            "query_id": result["query_id"],
            "result_count": len(result["items"]),
            "channel_counts": result["channel_counts"],
        }
        invocation.warnings = result["warnings"]
        invocation.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {
            **result,
            "request_id": request_id,
            "scenario": {"code": scenario.code, "version": version.version},
            "knowledge_product_release": {
                "id": product_release.id,
                "version": product_release.version,
                "checksum": product_release.checksum,
            },
        }
    except Exception as exc:
        db.rollback()
        invocation = db.get(ApplicationInvocation, invocation.id)
        if invocation:
            invocation.status = "failed"
            invocation.duration_ms = round((time.perf_counter() - started) * 1000)
            invocation.error_code = type(exc).__name__
            invocation.error_message = str(exc)[:1000]
            invocation.finished_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(status_code=502, detail=f"场景检索失败：{str(exc)[:300]}")


# ---- Application grants --------------------------------------------------------------


def _validate_grant_resource(db: Session, tenant_id: str, resource_type: str, resource_id: str) -> None:
    model = KnowledgeProduct if resource_type == "knowledge_product" else ApplicationScenario
    _must_tenant(db, model, resource_id, tenant_id, "授权资源")


@router.get("/applications/{application_id}/grants")
def list_application_grants(
    application_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _must_owned(db, Application, application_id, admin, "应用")
    return [
        serialize_row(row)
        for row in db.scalars(
            select(ApplicationGrant).where(
                ApplicationGrant.application_id == application_id,
                ApplicationGrant.tenant_id == admin.tenant_id,
                _active(ApplicationGrant),
            ).order_by(ApplicationGrant.resource_type, ApplicationGrant.created_at.desc())
        )
    ]


@router.post("/applications/{application_id}/grants")
def create_application_grant(
    application_id: str,
    payload: GrantCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _must_owned(db, Application, application_id, admin, "应用")
    _validate_grant_resource(db, admin.tenant_id, payload.resource_type, payload.resource_id)
    recycled = db.scalar(select(ApplicationGrant).where(
        ApplicationGrant.application_id == application_id,
        ApplicationGrant.resource_type == payload.resource_type,
        ApplicationGrant.resource_id == payload.resource_id,
        ApplicationGrant.permission == payload.permission,
    ))
    if recycled is not None:
        if recycled.deleted_at is None:
            raise HTTPException(status_code=409, detail="该资源授权已存在")
        recycled.deleted_at = None
        recycled.effect = payload.effect
        audit(
            db,
            admin.tenant_id,
            admin.id,
            "application.grant.restore",
            "application_grant",
            recycled.id,
        )
        db.commit()
        return serialize_row(recycled)
    row = ApplicationGrant(application_id=application_id, tenant_id=admin.tenant_id, **payload.model_dump())
    db.add(row)
    audit(db, admin.tenant_id, admin.id, "application.grant.create", "application_grant", row.id)
    _commit(db, "该资源授权已存在")
    return serialize_row(row)


@router.delete("/applications/{application_id}/grants/{grant_id}")
def delete_application_grant(
    application_id: str,
    grant_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _must_owned(db, Application, application_id, admin, "应用")
    row = _must_tenant(db, ApplicationGrant, grant_id, admin.tenant_id, "应用授权")
    if row.application_id != application_id:
        raise HTTPException(status_code=404, detail="应用授权不存在")
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "application.grant.delete", "application_grant", row.id)
    db.commit()
    return {"ok": True}


# ---- Knowledge products and immutable releases --------------------------------------


def _replace_product_spaces(
    db: Session,
    product: KnowledgeProduct,
    space_ids: list[str],
    actor: User,
) -> None:
    unique_ids = list(dict.fromkeys(space_ids))
    for space_id in unique_ids:
        _must_tenant(db, KnowledgeSpace, space_id, product.tenant_id, "知识空间")
        if not has_space_permission(db, actor, space_id, "read"):
            raise HTTPException(status_code=403, detail="知识供给包含无权读取的知识空间")
    db.query(KnowledgeProductSpace).filter(KnowledgeProductSpace.product_id == product.id).delete()
    for ordinal, space_id in enumerate(unique_ids):
        db.add(KnowledgeProductSpace(
            product_id=product.id,
            tenant_id=product.tenant_id,
            space_id=space_id,
            sort_order=ordinal,
        ))


def _product_view(db: Session, row: KnowledgeProduct) -> dict[str, Any]:
    data = serialize_row(row)
    data["space_ids"] = list(
        db.scalars(
            select(KnowledgeProductSpace.space_id).where(
                KnowledgeProductSpace.product_id == row.id,
                _active(KnowledgeProductSpace),
            ).order_by(KnowledgeProductSpace.sort_order)
        )
    )
    aliases = db.scalars(
        select(KnowledgeProductAlias).where(
            KnowledgeProductAlias.product_id == row.id,
            _active(KnowledgeProductAlias),
        )
    )
    data["aliases"] = {alias.alias: alias.product_release_id for alias in aliases}
    production_release_id = data["aliases"].get("production")
    release_items = {}
    if production_release_id:
        release_items = {
            item.space_id: item
            for item in db.scalars(select(KnowledgeProductReleaseItem).where(
                KnowledgeProductReleaseItem.product_release_id == production_release_id,
                _active(KnowledgeProductReleaseItem),
            ))
        }
    changed_spaces: list[dict[str, Any]] = []
    for space_id in data["space_ids"]:
        latest = db.scalar(select(KnowledgeRelease).where(
            KnowledgeRelease.tenant_id == row.tenant_id,
            KnowledgeRelease.space_id == space_id,
            KnowledgeRelease.status == "published",
            _active(KnowledgeRelease),
        ).order_by(KnowledgeRelease.release_number.desc()).limit(1))
        pinned = release_items.get(space_id)
        if latest and (pinned is None or pinned.knowledge_release_id != latest.id):
            space = db.get(KnowledgeSpace, space_id)
            changed_spaces.append({
                "space_id": space_id,
                "space_name": space.name if space else space_id,
                "pinned_release_id": pinned.knowledge_release_id if pinned else None,
                "latest_release_id": latest.id,
                "latest_release_number": latest.release_number,
            })
    data["release_freshness"] = {
        "has_production_release": bool(production_release_id),
        "is_current": bool(production_release_id) and not changed_spaces,
        "changed_spaces": changed_spaces,
    }
    return data


@router.get("/knowledge-products")
def list_products(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(KnowledgeProduct).where(KnowledgeProduct.tenant_id == user.tenant_id, _active(KnowledgeProduct))
    if not user.is_admin:
        query = query.where(KnowledgeProduct.owner_id == user.id)
    return [_product_view(db, row) for row in db.scalars(query.order_by(KnowledgeProduct.updated_at.desc()))]


@router.post("/knowledge-products")
def create_product(
    payload: KnowledgeProductCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(KnowledgeProduct.id).where(
        KnowledgeProduct.tenant_id == admin.tenant_id,
        KnowledgeProduct.code == payload.code,
        _active(KnowledgeProduct),
    ))
    if existing:
        raise HTTPException(status_code=409, detail="知识产品编码已存在")
    owner_id = payload.owner_id or admin.id
    if not admin.is_admin and owner_id != admin.id:
        raise HTTPException(status_code=403, detail="只能将知识供给负责人设置为当前用户")
    _validate_owner(db, admin.tenant_id, owner_id)
    row = KnowledgeProduct(
        tenant_id=admin.tenant_id,
        owner_id=owner_id,
        **payload.model_dump(exclude={"owner_id", "space_ids"}),
    )
    db.add(row)
    db.flush()
    _replace_product_spaces(db, row, payload.space_ids, admin)
    audit(db, admin.tenant_id, admin.id, "knowledge_product.create", "knowledge_product", row.id)
    _commit(db, "知识产品编码已存在")
    return _product_view(db, row)


@router.get("/knowledge-products/{row_id}")
def get_product(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, KnowledgeProduct, row_id, user.tenant_id, "知识产品")
    if not user.is_admin and row.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权查看该知识产品")
    return _product_view(db, row)


@router.put("/knowledge-products/{row_id}")
def update_product(
    row_id: str,
    payload: KnowledgeProductUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_owned(db, KnowledgeProduct, row_id, admin, "知识产品")
    values = payload.model_dump(exclude_unset=True)
    space_ids = values.pop("space_ids", None)
    if values.get("owner_id"):
        if not admin.is_admin and values["owner_id"] != admin.id:
            raise HTTPException(status_code=403, detail="不能将知识供给转交给其他用户")
        _validate_owner(db, admin.tenant_id, values["owner_id"])
    apply_patch(row, values, {"name", "description", "owner_id", "status", "config", "enabled"})
    if space_ids is not None:
        _replace_product_spaces(db, row, space_ids, admin)
    audit(db, admin.tenant_id, admin.id, "knowledge_product.update", "knowledge_product", row.id, values)
    db.commit()
    return _product_view(db, row)


@router.delete("/knowledge-products/{row_id}")
def delete_product(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_owned(db, KnowledgeProduct, row_id, admin, "知识产品")
    if db.scalar(select(func.count()).select_from(ApplicationScenarioVersion).where(ApplicationScenarioVersion.product_id == row.id)):
        raise HTTPException(status_code=409, detail="知识产品已被场景版本引用，不能删除")
    row.deleted_at = datetime.now(timezone.utc)
    row.enabled = False
    row.status = "retired"
    audit(db, admin.tenant_id, admin.id, "knowledge_product.delete", "knowledge_product", row.id)
    db.commit()
    return {"ok": True}


@router.get("/knowledge-products/{product_id}/releases")
def list_product_releases(
    product_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _must_owned(db, KnowledgeProduct, product_id, user, "知识产品")
    result = []
    for release in db.scalars(
        select(KnowledgeProductRelease).where(
            KnowledgeProductRelease.product_id == product_id,
            _active(KnowledgeProductRelease),
        ).order_by(KnowledgeProductRelease.version.desc())
    ):
        data = serialize_row(release)
        data["items"] = [
            serialize_row(item)
            for item in db.scalars(
                select(KnowledgeProductReleaseItem).where(
                    KnowledgeProductReleaseItem.product_release_id == release.id,
                    _active(KnowledgeProductReleaseItem),
                )
            )
        ]
        result.append(data)
    return result


@router.post("/knowledge-products/{product_id}/releases")
def create_product_release(
    product_id: str,
    payload: ProductReleaseCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    product = _must_owned(db, KnowledgeProduct, product_id, admin, "知识产品")
    space_ids = list(
        db.scalars(select(KnowledgeProductSpace.space_id).where(
            KnowledgeProductSpace.product_id == product.id,
            _active(KnowledgeProductSpace),
        ).order_by(KnowledgeProductSpace.sort_order))
    )
    if not space_ids:
        raise HTTPException(status_code=409, detail="请先为知识产品选择知识空间")
    items: list[KnowledgeRelease] = []
    for space_id in space_ids:
        current = db.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_id == admin.tenant_id,
                KnowledgeRelease.space_id == space_id,
                KnowledgeRelease.status == "published",
                _active(KnowledgeRelease),
            ).order_by(KnowledgeRelease.release_number.desc())
        )
        if current is None:
            space = db.get(KnowledgeSpace, space_id)
            raise HTTPException(status_code=409, detail=f"知识空间“{space.name if space else space_id}”尚无已发布知识版本")
        items.append(current)
    version = int(db.scalar(select(func.max(KnowledgeProductRelease.version)).where(
        KnowledgeProductRelease.product_id == product.id
    )) or 0) + 1
    manifest = {
        "product_code": product.code,
        "version": version,
        "note": payload.note,
        "knowledge_releases": [
            {"space_id": item.space_id, "knowledge_release_id": item.id, "checksum": item.checksum}
            for item in items
        ],
    }
    row = KnowledgeProductRelease(
        product_id=product.id,
        tenant_id=admin.tenant_id,
        version=version,
        manifest=manifest,
        checksum=_canonical_checksum(manifest),
        status="published",
        created_by=admin.id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    for item in items:
        db.add(KnowledgeProductReleaseItem(
            product_release_id=row.id,
            tenant_id=admin.tenant_id,
            space_id=item.space_id,
            knowledge_release_id=item.id,
            checksum=item.checksum,
        ))
    audit(db, admin.tenant_id, admin.id, "knowledge_product.release", "knowledge_product_release", row.id, manifest)
    _commit(db)
    return serialize_row(row)


@router.put("/knowledge-products/{product_id}/aliases/{alias}")
def move_product_alias(
    product_id: str,
    alias: str,
    payload: ProductAliasMove,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if alias not in {"development", "testing", "production"}:
        raise HTTPException(status_code=400, detail="别名仅支持 development、testing、production")
    _must_owned(db, KnowledgeProduct, product_id, admin, "知识产品")
    release = _must_tenant(db, KnowledgeProductRelease, payload.product_release_id, admin.tenant_id, "知识产品版本")
    if release.product_id != product_id or release.status != "published":
        raise HTTPException(status_code=409, detail="目标版本不属于当前知识产品或尚未发布")
    row = db.scalar(select(KnowledgeProductAlias).where(
        KnowledgeProductAlias.product_id == product_id,
        KnowledgeProductAlias.alias == alias,
        _active(KnowledgeProductAlias),
    ))
    from_release_id = row.product_release_id if row else None
    if row is None:
        row = KnowledgeProductAlias(
            product_id=product_id,
            tenant_id=admin.tenant_id,
            alias=alias,
            product_release_id=release.id,
            moved_by=admin.id,
        )
        db.add(row)
        db.flush()
    else:
        row.product_release_id = release.id
        row.moved_by = admin.id
    db.add(KnowledgeProductAliasHistory(
        alias_id=row.id,
        tenant_id=admin.tenant_id,
        from_release_id=from_release_id,
        to_release_id=release.id,
        moved_by=admin.id,
        reason=payload.reason,
    ))
    audit(db, admin.tenant_id, admin.id, "knowledge_product.alias.move", "knowledge_product_alias", row.id, {
        "alias": alias, "from_release_id": from_release_id, "to_release_id": release.id,
    })
    db.commit()
    return serialize_row(row)


# ---- Scenario configuration and immutable versions ----------------------------------


@router.get("/application-scenarios")
def list_scenarios(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(ApplicationScenario).where(
        ApplicationScenario.tenant_id == user.tenant_id,
        _active(ApplicationScenario),
    )
    if not user.is_admin:
        query = query.where(ApplicationScenario.owner_id == user.id)
    return [serialize_row(row) for row in db.scalars(query.order_by(ApplicationScenario.updated_at.desc()))]


@router.post("/application-scenarios")
def create_scenario(
    payload: ScenarioCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    owner_id = payload.owner_id or admin.id
    if not admin.is_admin and owner_id != admin.id:
        raise HTTPException(status_code=403, detail="只能将场景负责人设置为当前用户")
    _validate_owner(db, admin.tenant_id, owner_id)
    row = ApplicationScenario(
        tenant_id=admin.tenant_id,
        owner_id=owner_id,
        **payload.model_dump(exclude={"owner_id"}),
    )
    db.add(row)
    audit(db, admin.tenant_id, admin.id, "application_scenario.create", "application_scenario", row.id)
    _commit(db, "场景编码已存在")
    return serialize_row(row)


@router.put("/application-scenarios/{row_id}")
def update_scenario(
    row_id: str,
    payload: ScenarioUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_owned(db, ApplicationScenario, row_id, admin, "应用场景")
    values = payload.model_dump(exclude_unset=True)
    if values.get("owner_id"):
        if not admin.is_admin and values["owner_id"] != admin.id:
            raise HTTPException(status_code=403, detail="不能将场景转交给其他用户")
        _validate_owner(db, admin.tenant_id, values["owner_id"])
    apply_patch(row, values, {"name", "description", "scenario_type", "owner_id", "status", "enabled"})
    audit(db, admin.tenant_id, admin.id, "application_scenario.update", "application_scenario", row.id, values)
    db.commit()
    return serialize_row(row)


@router.delete("/application-scenarios/{row_id}")
def delete_scenario(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_owned(db, ApplicationScenario, row_id, admin, "应用场景")
    if db.scalar(select(func.count()).select_from(ApplicationGrant).where(
        ApplicationGrant.resource_type == "scenario",
        ApplicationGrant.resource_id == row.id,
        _active(ApplicationGrant),
    )):
        raise HTTPException(status_code=409, detail="场景仍有应用授权，请先移除授权")
    row.deleted_at = datetime.now(timezone.utc)
    row.enabled = False
    row.status = "retired"
    audit(db, admin.tenant_id, admin.id, "application_scenario.delete", "application_scenario", row.id)
    db.commit()
    return {"ok": True}


@router.get("/application-scenarios/{scenario_id}/versions")
def list_scenario_versions(
    scenario_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _must_owned(db, ApplicationScenario, scenario_id, user, "应用场景")
    return [
        serialize_row(row)
        for row in db.scalars(
            select(ApplicationScenarioVersion).where(
                ApplicationScenarioVersion.scenario_id == scenario_id,
                _active(ApplicationScenarioVersion),
            ).order_by(ApplicationScenarioVersion.version.desc())
        )
    ]


@router.post("/application-scenarios/{scenario_id}/versions")
def create_scenario_version(
    scenario_id: str,
    payload: ScenarioVersionCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    scenario = _must_owned(db, ApplicationScenario, scenario_id, admin, "应用场景")
    product = _must_owned(db, KnowledgeProduct, payload.product_id, admin, "知识产品")
    alias = db.scalar(select(KnowledgeProductAlias).where(
        KnowledgeProductAlias.product_id == product.id,
        KnowledgeProductAlias.alias == payload.product_alias,
        _active(KnowledgeProductAlias),
    ))
    if alias is None:
        raise HTTPException(status_code=409, detail=f"知识产品尚未配置 {payload.product_alias} 发布别名")
    if payload.model_config_id:
        model = _must_tenant(db, ModelConfig, payload.model_config_id, admin.tenant_id, "模型配置")
        if model.model_kind != "llm" or not model.enabled:
            raise HTTPException(status_code=409, detail="场景模型必须是已启用的大语言模型")
    for rule_set_id in payload.analysis_rule_set_ids:
        _must_tenant(db, AnalysisRuleSet, rule_set_id, admin.tenant_id, "分析规则集")
    version = int(db.scalar(select(func.max(ApplicationScenarioVersion.version)).where(
        ApplicationScenarioVersion.scenario_id == scenario.id
    )) or 0) + 1
    config = payload.model_dump()
    config.update({"scenario_code": scenario.code, "version": version})
    row = ApplicationScenarioVersion(
        scenario_id=scenario.id,
        tenant_id=admin.tenant_id,
        version=version,
        checksum=_canonical_checksum(config),
        status="published",
        created_by=admin.id,
        **payload.model_dump(),
    )
    db.add(row)
    db.flush()
    scenario.current_version_id = row.id
    if scenario.status == "draft":
        scenario.status = "active"
    audit(db, admin.tenant_id, admin.id, "application_scenario.version.create", "application_scenario_version", row.id, {
        "version": version, "checksum": row.checksum,
    })
    _commit(db)
    return serialize_row(row)


@router.get("/application-invocations")
def list_application_invocations(
    application_id: str | None = None,
    limit: int = 100,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(ApplicationInvocation).where(
        ApplicationInvocation.tenant_id == admin.tenant_id,
        _active(ApplicationInvocation),
    )
    if application_id:
        application = _must_owned(db, Application, application_id, admin, "应用")
        query = query.where(ApplicationInvocation.application_id == application.id)
    elif not admin.is_admin:
        owned_ids = select(Application.id).where(
            Application.tenant_id == admin.tenant_id,
            Application.owner_id == admin.id,
            _active(Application),
        )
        query = query.where(ApplicationInvocation.application_id.in_(owned_ids))
    limit = max(1, min(limit, 500))
    return [serialize_row(row) for row in db.scalars(query.order_by(ApplicationInvocation.created_at.desc()).limit(limit))]


@router.get("/application-foundation/capabilities")
def foundation_capabilities(user: User = Depends(get_current_user)):
    return {
        "application_identity": True,
        "service_credentials": True,
        "knowledge_products": True,
        "immutable_product_releases": True,
        "release_aliases": ["development", "testing", "production"],
        "scenario_versions": True,
        "evaluation": True,
        "feedback_loop": True,
        "application_runtime": ["whoami", "scenario_search", "feedback"],
        "token_ttl_minutes": get_settings().application_access_token_minutes,
    }
