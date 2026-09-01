from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.application_schemas import (
    EvaluationCaseCreate,
    EvaluationCaseUpdate,
    EvaluationDatasetCreate,
    EvaluationDatasetUpdate,
    EvaluationRunCreate,
    FeedbackCreate,
    FeedbackUpdate,
)
from apps.api.deps import ApplicationPrincipal, get_current_application, get_current_user, require_admin
from apps.api.utils import apply_patch, serialize_row
from packages.platform.application_services import (
    ApplicationConfigurationError,
    aggregate_evaluation_metrics,
    evaluation_gate_passed,
    resolve_scenario_product_release,
    retrieval_metrics,
)
from packages.platform.audit import audit
from packages.platform.curation import stable_fingerprint
from packages.platform.database import get_db
from packages.platform.knowledge_search import execute_hybrid_search
from packages.platform.models import (
    Application,
    ApplicationFeedback,
    ApplicationInvocation,
    ApplicationScenario,
    ApplicationScenarioVersion,
    Chunk,
    CurationCase,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    KnowledgeProductReleaseItem,
    User,
)


router = APIRouter(tags=["application-quality"])


def _active(model: type):
    return model.deleted_at.is_(None)


def _must_tenant(db: Session, model: type, row_id: str, tenant_id: str, label: str):
    row = db.get(model, row_id)
    if row is None or row.deleted_at is not None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return row


def _commit(db: Session, message: str = "编码已存在") -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=message)


# ---- Evaluation datasets, cases and deterministic runs -------------------------------


@router.get("/evaluation-datasets")
def list_evaluation_datasets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(EvaluationDataset).where(EvaluationDataset.tenant_id == user.tenant_id, _active(EvaluationDataset))
    if not user.is_admin:
        query = query.where(EvaluationDataset.owner_id == user.id)
    result = []
    for row in db.scalars(query.order_by(EvaluationDataset.updated_at.desc())):
        data = serialize_row(row)
        data["case_count"] = db.scalar(select(func.count()).select_from(EvaluationCase).where(
            EvaluationCase.dataset_id == row.id,
            _active(EvaluationCase),
        ))
        result.append(data)
    return result


@router.post("/evaluation-datasets")
def create_evaluation_dataset(
    payload: EvaluationDatasetCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = EvaluationDataset(tenant_id=admin.tenant_id, owner_id=admin.id, **payload.model_dump())
    db.add(row)
    audit(db, admin.tenant_id, admin.id, "evaluation.dataset.create", "evaluation_dataset", row.id)
    _commit(db, "评测集编码已存在")
    return serialize_row(row)


@router.put("/evaluation-datasets/{row_id}")
def update_evaluation_dataset(
    row_id: str,
    payload: EvaluationDatasetUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, EvaluationDataset, row_id, admin.tenant_id, "评测集")
    values = payload.model_dump(exclude_unset=True)
    apply_patch(row, values, {"name", "description", "enabled"})
    audit(db, admin.tenant_id, admin.id, "evaluation.dataset.update", "evaluation_dataset", row.id, values)
    db.commit()
    return serialize_row(row)


@router.delete("/evaluation-datasets/{row_id}")
def delete_evaluation_dataset(
    row_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, EvaluationDataset, row_id, admin.tenant_id, "评测集")
    if db.scalar(select(func.count()).select_from(EvaluationRun).where(EvaluationRun.dataset_id == row.id)):
        raise HTTPException(status_code=409, detail="评测集已有运行记录，不能删除；可停用保留历史")
    row.deleted_at = datetime.now(timezone.utc)
    row.enabled = False
    audit(db, admin.tenant_id, admin.id, "evaluation.dataset.delete", "evaluation_dataset", row.id)
    db.commit()
    return {"ok": True}


@router.get("/evaluation-datasets/{dataset_id}/cases")
def list_evaluation_cases(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _must_tenant(db, EvaluationDataset, dataset_id, user.tenant_id, "评测集")
    return [serialize_row(row) for row in db.scalars(select(EvaluationCase).where(
        EvaluationCase.dataset_id == dataset_id,
        _active(EvaluationCase),
    ).order_by(EvaluationCase.case_key))]


@router.post("/evaluation-datasets/{dataset_id}/cases")
def create_evaluation_case(
    dataset_id: str,
    payload: EvaluationCaseCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _must_tenant(db, EvaluationDataset, dataset_id, admin.tenant_id, "评测集")
    row = EvaluationCase(dataset_id=dataset_id, tenant_id=admin.tenant_id, **payload.model_dump())
    db.add(row)
    audit(db, admin.tenant_id, admin.id, "evaluation.case.create", "evaluation_case", row.id)
    _commit(db, "评测用例编号已存在")
    return serialize_row(row)


@router.put("/evaluation-cases/{row_id}")
def update_evaluation_case(
    row_id: str,
    payload: EvaluationCaseUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, EvaluationCase, row_id, admin.tenant_id, "评测用例")
    values = payload.model_dump(exclude_unset=True)
    apply_patch(row, values, {
        "question", "expected_answer", "expected_chunk_ids", "expected_facts",
        "expected_schema", "tags", "enabled",
    })
    audit(db, admin.tenant_id, admin.id, "evaluation.case.update", "evaluation_case", row.id, values)
    db.commit()
    return serialize_row(row)


@router.delete("/evaluation-cases/{row_id}")
def delete_evaluation_case(
    row_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, EvaluationCase, row_id, admin.tenant_id, "评测用例")
    if db.scalar(select(func.count()).select_from(EvaluationCaseResult).where(EvaluationCaseResult.case_id == row.id)):
        raise HTTPException(status_code=409, detail="评测用例已有运行结果，不能删除；可停用保留历史")
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "evaluation.case.delete", "evaluation_case", row.id)
    db.commit()
    return {"ok": True}


@router.get("/evaluation-runs")
def list_evaluation_runs(
    dataset_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(EvaluationRun).where(EvaluationRun.tenant_id == user.tenant_id, _active(EvaluationRun))
    if dataset_id:
        query = query.where(EvaluationRun.dataset_id == dataset_id)
    return [serialize_row(row) for row in db.scalars(query.order_by(EvaluationRun.created_at.desc()).limit(200))]


@router.get("/evaluation-runs/{row_id}")
def get_evaluation_run(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, EvaluationRun, row_id, user.tenant_id, "评测运行")
    data = serialize_row(row)
    data["results"] = [serialize_row(item) for item in db.scalars(select(EvaluationCaseResult).where(
        EvaluationCaseResult.run_id == row.id,
        _active(EvaluationCaseResult),
    ).order_by(EvaluationCaseResult.created_at))]
    return data


@router.post("/evaluation-runs")
def run_evaluation(
    payload: EvaluationRunCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    dataset = _must_tenant(db, EvaluationDataset, payload.dataset_id, admin.tenant_id, "评测集")
    version = _must_tenant(db, ApplicationScenarioVersion, payload.scenario_version_id, admin.tenant_id, "场景版本")
    if not dataset.enabled:
        raise HTTPException(status_code=409, detail="评测集已停用")
    try:
        _, space_ids = resolve_scenario_product_release(db, version)
    except ApplicationConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        evaluation_gate_passed({}, payload.gate_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cases = list(db.scalars(select(EvaluationCase).where(
        EvaluationCase.dataset_id == dataset.id,
        EvaluationCase.enabled.is_(True),
        _active(EvaluationCase),
    ).order_by(EvaluationCase.case_key)))
    if not cases:
        raise HTTPException(status_code=409, detail="评测集没有已启用的用例")
    row = EvaluationRun(
        tenant_id=admin.tenant_id,
        dataset_id=dataset.id,
        scenario_version_id=version.id,
        status="running",
        progress=0,
        gate_config=payload.gate_config,
        created_by=admin.id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    policy = version.retrieval_policy or {}
    all_metrics = []
    failed = 0
    for index, case in enumerate(cases, start=1):
        try:
            result = execute_hybrid_search(
                db,
                tenant_id=admin.tenant_id,
                user_id=admin.id,
                query=case.question,
                space_ids=space_ids,
                top_k=max(1, min(int(policy.get("top_k", 8)), 100)),
                use_keyword=bool(policy.get("use_keyword", True)),
                use_vector=bool(policy.get("use_vector", True)),
                use_graph=bool(policy.get("use_graph", True)),
                use_reranker=bool(policy.get("use_reranker", False)),
                filters={},
                audit_action="evaluation.retrieval",
            )
            retrieved_ids = [str(item["chunk_id"]) for item in result["items"]]
            metrics = retrieval_metrics(retrieved_ids, case.expected_chunk_ids or [], k=int(policy.get("top_k", 8)))
            all_metrics.append(metrics)
            db.add(EvaluationCaseResult(
                run_id=row.id,
                case_id=case.id,
                tenant_id=admin.tenant_id,
                status="succeeded",
                retrieved_chunk_ids=retrieved_ids,
                citations=[],
                metrics=metrics,
                trace={
                    "query_id": result["query_id"],
                    "trace_summary": result["trace_summary"],
                    "warnings": result["warnings"],
                },
            ))
        except Exception as exc:
            failed += 1
            db.rollback()
            row = db.get(EvaluationRun, row.id)
            case = db.get(EvaluationCase, case.id)
            db.add(EvaluationCaseResult(
                run_id=row.id,
                case_id=case.id,
                tenant_id=admin.tenant_id,
                status="failed",
                metrics={},
                trace={},
                error_message=f"{type(exc).__name__}: {str(exc)[:900]}",
            ))
        row.progress = round(index / len(cases) * 100)
        db.commit()
    aggregate = aggregate_evaluation_metrics(all_metrics)
    aggregate["failed_count"] = failed
    row = db.get(EvaluationRun, row.id)
    row.metrics = aggregate
    row.gate_passed = failed == 0 and evaluation_gate_passed(aggregate, payload.gate_config)
    row.status = "succeeded" if failed == 0 else "partial"
    row.progress = 100
    row.finished_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "evaluation.run", "evaluation_run", row.id, aggregate)
    db.commit()
    return serialize_row(row)


# ---- Application feedback and curation conversion -----------------------------------


def _validate_feedback_links(db: Session, tenant_id: str, application_id: str, payload: FeedbackCreate) -> None:
    if payload.scenario_id:
        _must_tenant(db, ApplicationScenario, payload.scenario_id, tenant_id, "应用场景")
    if payload.invocation_id:
        invocation = _must_tenant(db, ApplicationInvocation, payload.invocation_id, tenant_id, "调用记录")
        if invocation.application_id != application_id:
            raise HTTPException(status_code=409, detail="调用记录不属于当前应用")


def _create_feedback(
    db: Session,
    *,
    application: Application,
    payload: FeedbackCreate,
    submitted_by: str | None,
) -> ApplicationFeedback:
    _validate_feedback_links(db, application.tenant_id, application.id, payload)
    row = ApplicationFeedback(
        tenant_id=application.tenant_id,
        application_id=application.id,
        submitted_by=submitted_by,
        **payload.model_dump(),
    )
    db.add(row)
    return row


@router.post("/application-runtime/feedback")
def submit_application_feedback(
    payload: FeedbackCreate,
    principal: ApplicationPrincipal = Depends(get_current_application),
    db: Session = Depends(get_db),
):
    if "feedback.write" not in principal.scopes and "*" not in principal.scopes:
        raise HTTPException(status_code=403, detail="应用缺少权限：feedback.write")
    row = _create_feedback(db, application=principal.application, payload=payload, submitted_by=None)
    audit(db, principal.tenant_id, None, "application.feedback.create", "application_feedback", row.id, {
        "application_id": principal.application.id, "feedback_type": payload.feedback_type,
    })
    db.commit()
    return {"id": row.id, "status": row.status, "created_at": row.created_at}


@router.post("/application-feedback")
def create_user_feedback(
    application_id: str,
    payload: FeedbackCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    application = _must_tenant(db, Application, application_id, user.tenant_id, "应用")
    row = _create_feedback(db, application=application, payload=payload, submitted_by=user.id)
    audit(db, user.tenant_id, user.id, "application.feedback.create", "application_feedback", row.id)
    db.commit()
    return serialize_row(row)


@router.get("/application-feedback")
def list_application_feedback(
    application_id: str | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(ApplicationFeedback).where(
        ApplicationFeedback.tenant_id == admin.tenant_id,
        _active(ApplicationFeedback),
    )
    if application_id:
        query = query.where(ApplicationFeedback.application_id == application_id)
    if status:
        query = query.where(ApplicationFeedback.status == status)
    return [serialize_row(row) for row in db.scalars(query.order_by(ApplicationFeedback.created_at.desc()).limit(limit))]


@router.put("/application-feedback/{row_id}")
def update_application_feedback(
    row_id: str,
    payload: FeedbackUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, ApplicationFeedback, row_id, admin.tenant_id, "应用反馈")
    values = payload.model_dump(exclude_unset=True)
    apply_patch(row, values, {"status", "comment"})
    audit(db, admin.tenant_id, admin.id, "application.feedback.update", "application_feedback", row.id, values)
    db.commit()
    return serialize_row(row)


@router.delete("/application-feedback/{row_id}")
def delete_application_feedback(
    row_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, ApplicationFeedback, row_id, admin.tenant_id, "应用反馈")
    if row.curation_case_id:
        raise HTTPException(status_code=409, detail="反馈已转为治理任务，不能删除")
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "application.feedback.delete", "application_feedback", row.id)
    db.commit()
    return {"ok": True}


@router.post("/application-feedback/{row_id}/convert-to-curation")
def convert_feedback_to_curation(
    row_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, ApplicationFeedback, row_id, admin.tenant_id, "应用反馈")
    if row.curation_case_id:
        existing = db.get(CurationCase, row.curation_case_id)
        return serialize_row(existing) if existing else {"id": row.curation_case_id}
    evidence = dict(row.evidence or {})
    chunk_id = evidence.get("chunk_id")
    chunk = db.get(Chunk, chunk_id) if chunk_id else None
    space_id = chunk.space_id if chunk else evidence.get("space_id")
    if not space_id and row.product_release_id:
        space_id = db.scalar(select(KnowledgeProductReleaseItem.space_id).where(
            KnowledgeProductReleaseItem.product_release_id == row.product_release_id
        ).limit(1))
    if not space_id:
        raise HTTPException(status_code=409, detail="反馈缺少可定位的知识空间，暂不能转治理任务")
    fingerprint = stable_fingerprint({"application_feedback_id": row.id})
    existing = db.scalar(select(CurationCase).where(
        CurationCase.tenant_id == admin.tenant_id,
        CurationCase.fingerprint == fingerprint,
        _active(CurationCase),
    ))
    case = existing or CurationCase(
        tenant_id=admin.tenant_id,
        space_id=space_id,
        document_id=chunk.document_id if chunk else None,
        version_id=chunk.version_id if chunk else None,
        target_type="application_feedback",
        target_id=row.id,
        case_type="application_feedback",
        severity="medium",
        title=f"应用反馈：{row.feedback_type}",
        reason=row.comment or "应用调用产生的知识质量反馈",
        evidence={
            **evidence,
            "application_id": row.application_id,
            "scenario_id": row.scenario_id,
            "invocation_id": row.invocation_id,
            "rating": row.rating,
        },
        fingerprint=fingerprint,
        status="open",
    )
    if existing is None:
        db.add(case)
        db.flush()
    row.curation_case_id = case.id
    row.status = "converted"
    audit(db, admin.tenant_id, admin.id, "application.feedback.convert", "application_feedback", row.id, {
        "curation_case_id": case.id,
    })
    db.commit()
    return serialize_row(case)


@router.get("/application-quality/capabilities")
def quality_capabilities(user: User = Depends(get_current_user)):
    return {
        "evaluation_metrics": ["recall_at_k", "mrr", "ndcg_at_k"],
        "quality_gates": True,
        "feedback_to_curation": True,
        "llm_judge": False,
        "note": "当前质量门禁使用可重复的确定性指标；模型裁判未启用。",
    }

