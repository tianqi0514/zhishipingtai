from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.application_quality import verify_feedback_resolution
from apps.api.application_schemas import FeedbackResolutionVerify
from apps.api.routes import update_curation_case
from apps.api.schemas import CurationCaseUpdate
from packages.platform.database import Base
from packages.platform.models import (
    Application,
    ApplicationFeedback,
    ApplicationScenario,
    ApplicationScenarioVersion,
    CurationCase,
    EvaluationDataset,
    EvaluationRun,
    KnowledgeProduct,
    KnowledgeSpace,
    Tenant,
    User,
)
from packages.platform.security import hash_password


def test_handling_linked_curation_case_moves_feedback_to_verification_and_reopens_it() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="feedback-loop", name="反馈闭环租户")
        db.add(tenant)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            username="governor",
            password_hash=hash_password("Governance@123"),
            display_name="知识治理员",
            is_admin=True,
            enabled=True,
        )
        db.add(user)
        db.flush()
        space = KnowledgeSpace(
            tenant_id=tenant.id,
            code="feedback-space",
            name="反馈知识空间",
            owner_id=user.id,
            enabled=True,
        )
        application = Application(
            tenant_id=tenant.id,
            owner_id=user.id,
            code="feedback-app",
            name="反馈闭环应用",
            status="active",
        )
        db.add_all([space, application])
        db.flush()
        feedback = ApplicationFeedback(
            tenant_id=tenant.id,
            application_id=application.id,
            feedback_type="outdated",
            comment="制度已经更新",
            status="converted",
        )
        db.add(feedback)
        db.flush()
        case = CurationCase(
            tenant_id=tenant.id,
            space_id=space.id,
            target_type="application_feedback",
            target_id=feedback.id,
            case_type="application_feedback",
            title="应用反馈：知识过期",
            fingerprint="f" * 64,
            status="open",
        )
        db.add(case)
        db.flush()
        feedback.curation_case_id = case.id
        db.commit()

        update_curation_case(
            case.id,
            CurationCaseUpdate(status="handled", resolution="corrected", reason_note="已更新制度版本"),
            user,
            db,
        )
        assert db.get(ApplicationFeedback, feedback.id).status == "triaged"

        update_curation_case(
            case.id,
            CurationCaseUpdate(status="open", resolution="reopened", reason_note="需要补充验证"),
            user,
            db,
        )
        assert db.get(ApplicationFeedback, feedback.id).status == "converted"

        update_curation_case(
            case.id,
            CurationCaseUpdate(status="handled", resolution="corrected", reason_note="重新处理完成"),
            user,
            db,
        )
        product = KnowledgeProduct(
            tenant_id=tenant.id,
            code="feedback-product",
            name="反馈知识供给",
            owner_id=user.id,
            status="active",
        )
        scenario = ApplicationScenario(
            tenant_id=tenant.id,
            code="feedback-scenario",
            name="反馈验证场景",
            owner_id=user.id,
            status="active",
        )
        dataset = EvaluationDataset(
            tenant_id=tenant.id,
            code="feedback-dataset",
            name="反馈回归集",
            owner_id=user.id,
        )
        db.add_all([product, scenario, dataset])
        db.flush()
        version = ApplicationScenarioVersion(
            scenario_id=scenario.id,
            tenant_id=tenant.id,
            version=1,
            product_id=product.id,
            checksum="a" * 64,
            created_by=user.id,
        )
        db.add(version)
        db.flush()
        run = EvaluationRun(
            tenant_id=tenant.id,
            dataset_id=dataset.id,
            scenario_version_id=version.id,
            status="succeeded",
            progress=100,
            gate_passed=True,
            created_by=user.id,
        )
        db.add(run)
        db.commit()
        verified = verify_feedback_resolution(
            feedback.id,
            FeedbackResolutionVerify(evaluation_run_id=run.id),
            user,
            db,
        )
        assert verified["status"] == "resolved"
        assert verified["evidence"]["resolution_verification"]["evaluation_run_id"] == run.id
