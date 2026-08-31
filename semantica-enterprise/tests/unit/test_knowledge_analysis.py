from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.routes import create_analysis_rule, create_analysis_rule_set
from apps.api.schemas import AnalysisRuleCreate, AnalysisRuleSetCreate
from apps.worker.tasks import _queue_automatic_inference
from packages.platform.analysis import execute_inference_run, inference_result_rows
from packages.platform.database import Base
from packages.platform.models import (
    AnalysisRuleSet,
    CanonicalEntity,
    Fact,
    InferenceEvidence,
    InferenceRun,
    Job,
    KnowledgeSpace,
    Tenant,
    User,
)


def test_rule_crud_and_persisted_inference_are_tenant_scoped() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(code="analysis", name="知识分析租户")
        db.add(tenant)
        db.flush()
        admin = User(
            tenant_id=tenant.id,
            username="analysis-admin",
            password_hash="unused",
            display_name="分析管理员",
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        space = KnowledgeSpace(
            tenant_id=tenant.id,
            code="analysis-space",
            name="分析空间",
            owner_id=admin.id,
        )
        db.add(space)
        db.commit()

        created_set = create_analysis_rule_set(
            AnalysisRuleSetCreate(name="制度适用", space_ids=[space.id]), admin, db
        )
        created_rule = create_analysis_rule(
            created_set["id"],
            AnalysisRuleCreate(
                name="集团制度适用下属企业",
                definition={
                    "conditions": [
                        {"predicate": "属于", "subject": "X", "object": "G"},
                        {"predicate": "适用对象", "subject": "P", "object": "G"},
                    ],
                    "conclusion": {"predicate": "适用于", "subject": "P", "object": "X"},
                },
                confidence=0.9,
            ),
            admin,
            db,
        )
        assert created_rule["current_version"] == 1
        create_analysis_rule(
            created_set["id"],
            AnalysisRuleCreate(
                name="适用结论形成影响",
                definition={
                    "conditions": [{"predicate": "适用于", "subject": "P", "object": "X"}],
                    "conclusion": {"predicate": "影响", "subject": "P", "object": "X"},
                },
                confidence=1,
            ),
            admin,
            db,
        )

        company = CanonicalEntity(
            tenant_id=tenant.id,
            space_id=space.id,
            canonical_name="国联证券",
            normalized_name="国联证券",
            entity_type="企业",
        )
        group = CanonicalEntity(
            tenant_id=tenant.id,
            space_id=space.id,
            canonical_name="国联集团",
            normalized_name="国联集团",
            entity_type="集团",
        )
        policy = CanonicalEntity(
            tenant_id=tenant.id,
            space_id=space.id,
            canonical_name="数据治理办法",
            normalized_name="数据治理办法",
            entity_type="制度",
        )
        db.add_all([company, group, policy])
        db.flush()
        db.add_all(
            [
                Fact(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_entity_id=company.id,
                    predicate="属于",
                    object_entity_id=group.id,
                    confidence=1,
                ),
                Fact(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    subject_entity_id=policy.id,
                    predicate="适用对象",
                    object_entity_id=group.id,
                    confidence=0.8,
                ),
            ]
        )
        rule_set = db.get(AnalysisRuleSet, created_set["id"])
        run = InferenceRun(
            tenant_id=tenant.id,
            rule_set_id=rule_set.id,
            requested_by=admin.id,
            mode="preview",
            space_ids=[space.id],
            run_input={"max_results": 20},
        )
        db.add(run)
        db.commit()

        result = execute_inference_run(db, run.id)
        rows = inference_result_rows(db, run.id)

        assert result["persisted_results"] == 2
        applicable = next(item for item in rows if item["predicate"] == "适用于")
        impact = next(item for item in rows if item["predicate"] == "影响")
        assert applicable["subject_name"] == "数据治理办法"
        assert applicable["object_name"] == "国联证券"
        assert len(applicable["evidence"]) == 2
        linked_evidence = db.scalar(
            select(InferenceEvidence).where(
                InferenceEvidence.inferred_fact_id == impact["id"],
                InferenceEvidence.premise_type == "inferred",
            )
        )
        assert linked_evidence.source_inferred_fact_id == applicable["id"]


def test_document_publish_queues_each_automatic_rule_set_once() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(code="auto-analysis", name="自动分析租户")
        db.add(tenant)
        db.flush()
        admin = User(
            tenant_id=tenant.id,
            username="auto-analysis-admin",
            password_hash="unused",
            display_name="自动分析管理员",
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        space = KnowledgeSpace(
            tenant_id=tenant.id,
            code="auto-analysis-space",
            name="自动分析空间",
            owner_id=admin.id,
        )
        db.add(space)
        db.flush()
        rule_set = AnalysisRuleSet(
            tenant_id=tenant.id,
            name="自动发布规则",
            space_ids=[space.id],
            auto_run=True,
            auto_publish=True,
        )
        db.add(rule_set)
        db.commit()

        with patch("apps.worker.tasks.run_inference_task.delay") as delay:
            first = _queue_automatic_inference(
                db,
                tenant_id=tenant.id,
                space_id=space.id,
                document_id="document-1",
                version_id="version-1",
            )
            second = _queue_automatic_inference(
                db,
                tenant_id=tenant.id,
                space_id=space.id,
                document_id="document-1",
                version_id="version-1",
            )

        assert first == second
        assert len(first) == 1
        assert delay.call_count == 1
        run = db.get(InferenceRun, first[0])
        assert run.trigger_type == "document_update"
        assert run.mode == "publish"
        assert run.run_input["version_id"] == "version-1"
        assert len(list(db.scalars(select(Job).where(Job.job_type == "knowledge_inference")))) == 1
