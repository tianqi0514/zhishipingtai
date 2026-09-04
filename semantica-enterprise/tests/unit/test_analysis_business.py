from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.routes import (
    create_guided_analysis_setup,
    delete_analysis_rule_set,
    get_analysis_run_diagnostics,
    get_analysis_run_impact,
)
from apps.api.schemas import GuidedAnalysisSetupCreate
from packages.semantica_adapter.analyze import run_readonly_sparql
from packages.platform.analysis import execute_inference_run, inference_result_rows
from packages.platform.analysis_business import (
    analysis_readiness,
    analysis_vocabulary,
    preview_rule_matches,
    run_comparison,
    templates_for_vocabulary,
)
from packages.platform.database import Base
from packages.platform.models import (
    AnalysisRule,
    AnalysisRuleSet,
    AnalysisScenario,
    CanonicalEntity,
    Fact,
    InferenceRun,
    Job,
    KnowledgeSpace,
    Tenant,
    User,
)


def _business_fixture(db: Session):
    tenant = Tenant(code="analysis-business", name="知识分析验收租户")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="analysis-business-admin",
        password_hash="unused",
        display_name="分析管理员",
        is_admin=True,
    )
    db.add(admin)
    db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="analysis-acceptance",
        name="知识分析验收空间",
        owner_id=admin.id,
    )
    db.add(space)
    db.flush()
    policy = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=space.id,
        canonical_name="采购管理制度",
        normalized_name="采购管理制度",
        entity_type="制度",
    )
    group = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=space.id,
        canonical_name="国联集团",
        normalized_name="国联集团",
        entity_type="组织",
    )
    company = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=space.id,
        canonical_name="数字科技公司",
        normalized_name="数字科技公司",
        entity_type="组织",
    )
    db.add_all([policy, group, company])
    db.flush()
    db.add_all(
        [
            Fact(
                tenant_id=tenant.id,
                space_id=space.id,
                subject_entity_id=policy.id,
                predicate="适用于",
                object_entity_id=group.id,
                confidence=1,
            ),
            Fact(
                tenant_id=tenant.id,
                space_id=space.id,
                subject_entity_id=group.id,
                predicate="管理",
                object_entity_id=company.id,
                confidence=1,
            ),
        ]
    )
    db.commit()
    return tenant, admin, space, policy, group, company


def test_readiness_vocabulary_and_templates_use_published_graph_facts() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant, _, space, *_ = _business_fixture(db)

        vocabulary = analysis_vocabulary(db, tenant_id=tenant.id, space_id=space.id)
        readiness = analysis_readiness(db, tenant_id=tenant.id, space_id=space.id)
        templates = templates_for_vocabulary(vocabulary)

        assert vocabulary["entity_count"] == 3
        assert vocabulary["asserted_fact_count"] == 2
        assert {item["name"] for item in vocabulary["predicates"]} == {"适用于", "管理"}
        assert readiness["ready"] is True
        assert readiness["evidence_coverage"] == 0
        assert any(item["code"] == "low_evidence_coverage" for item in readiness["warnings"])
        policy_template = next(item for item in templates if item["id"] == "policy_scope")
        assert policy_template["ready"] is True
        assert policy_template["missing_predicates"] == []


def test_match_preview_executes_semantica_and_diagnoses_missing_relationship() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant, _, space, *_ = _business_fixture(db)
        definition = {
            "conditions": [
                {"predicate": "适用于", "subject": "R1", "object": "R2"},
                {"predicate": "管理", "subject": "R2", "object": "R3"},
            ],
            "conclusion": {"predicate": "适用于", "subject": "R1", "object": "R3"},
        }

        result = preview_rule_matches(
            db,
            tenant_id=tenant.id,
            space_id=space.id,
            definition=definition,
            confidence=1,
            max_results=100,
        )

        assert result["engine"] == "semantica.reasoning.DatalogReasoner"
        assert result["predicted_count"] == 1
        assert result["samples"][0]["subject"] == "采购管理制度"
        assert result["samples"][0]["object"] == "数字科技公司"

        missing = preview_rule_matches(
            db,
            tenant_id=tenant.id,
            space_id=space.id,
            definition={
                "conditions": [{"predicate": "供应", "subject": "R1", "object": "R2"}],
                "conclusion": {"predicate": "影响", "subject": "R1", "object": "R2"},
            },
            confidence=1,
            max_results=100,
        )
        assert missing["predicted_count"] == 0
        assert missing["diagnostics"] == [
            {"code": "missing_predicate", "message": "当前知识中没有关系：供应。"}
        ]


def test_guided_setup_atomically_creates_task_rule_version_run_and_real_results() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _, admin, space, *_ = _business_fixture(db)
        payload = GuidedAnalysisSetupCreate(
            name="制度适用范围验收",
            question="采购管理制度是否适用于数字科技公司？",
            category="制度治理",
            space_id=space.id,
            template_id="policy_scope",
            rule_name="制度适用范围判断",
            role_labels={"R1": "制度", "R2": "上级组织", "R3": "下属单位"},
            definition={
                "conditions": [
                    {"predicate": "适用于", "subject": "R1", "object": "R2"},
                    {"predicate": "管理", "subject": "R2", "object": "R3"},
                ],
                "conclusion": {"predicate": "适用于", "subject": "R1", "object": "R3"},
            },
            mode="preview",
        )

        with patch("apps.api.routes.run_inference_task.delay") as dispatch:
            created = create_guided_analysis_setup(payload, admin, db)

        assert dispatch.call_count == 1
        assert created["match_preview"]["predicted_count"] == 1
        assert db.scalar(select(AnalysisScenario).where(AnalysisScenario.id == created["task"]["id"]))
        assert db.scalar(select(AnalysisRuleSet).where(AnalysisRuleSet.id == created["rule_set"]["id"]))
        assert db.scalar(select(AnalysisRule).where(AnalysisRule.id == created["rule"]["id"]))
        assert db.scalar(select(Job).where(Job.id == created["run"]["job_id"]))

        execute_inference_run(db, created["run"]["id"])
        results = inference_result_rows(db, created["run"]["id"])
        assert [(item["subject_name"], item["predicate"], item["object_name"]) for item in results] == [
            ("采购管理制度", "适用于", "数字科技公司")
        ]
        diagnostics = get_analysis_run_diagnostics(created["run"]["id"], admin, db)
        impact = get_analysis_run_impact(created["run"]["id"], admin, db)
        assert diagnostics["issues"][0]["code"] == "evidence_missing"
        assert impact["can_publish"] is True
        assert impact["new_count"] == 1

        with pytest.raises(HTTPException) as dependent_delete:
            delete_analysis_rule_set(created["rule_set"]["id"], admin, db)
        assert dependent_delete.value.status_code == 409
        assert "制度适用范围验收" in str(dependent_delete.value.detail)

        management = db.scalar(select(Fact).where(Fact.space_id == space.id, Fact.predicate == "管理"))
        management.status = "suppressed"
        second = InferenceRun(
            tenant_id=admin.tenant_id,
            rule_set_id=created["rule_set"]["id"],
            scenario_id=created["task"]["id"],
            requested_by=admin.id,
            mode="preview",
            space_ids=[space.id],
            run_input={"max_results": 100},
        )
        db.add(second)
        db.commit()
        execute_inference_run(db, second.id)
        comparison = run_comparison(db, second)
        assert comparison["invalidated_count"] == 1
        assert comparison["invalidated_items"][0]["subject_name"] == "采购管理制度"
        assert comparison["invalidated_items"][0]["object_name"] == "数字科技公司"
        assert comparison["invalidated_items"][0]["previous_run_id"] == created["run"]["id"]


def test_sparql_business_predicate_label_is_queryable() -> None:
    result = run_readonly_sparql(
        entities=[
            {"id": "policy", "name": "采购管理制度", "type": "制度"},
            {"id": "group", "name": "国联集团", "type": "组织"},
        ],
        facts=[
            {
                "id": "fact-1",
                "subject_entity_id": "policy",
                "predicate": "适用于",
                "object_entity_id": "group",
            }
        ],
        inferred_facts=[],
        query=(
            'SELECT ?s ?p ?o WHERE { ?s ?p ?o . '
            '?p <http://www.w3.org/2000/01/rdf-schema#label> "适用于" } LIMIT 20'
        ),
    )

    assert result["total"] == 1
    assert result["rows"] == [
        {"s": "采购管理制度", "p": "适用于", "o": "国联集团"}
    ]
