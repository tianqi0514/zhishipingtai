from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.routes import _dispatch_job
from apps.api.routes import delete_document
from packages.platform.database import Base
from packages.platform.models import Document, DocumentVersion, Job, KnowledgeSpace, Tenant, User


def test_queue_publish_failure_becomes_a_retryable_job() -> None:
    job = SimpleNamespace(
        id="job-1",
        job_type="process_knowledge",
        input={"version_id": "version-1"},
        status="queued",
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    task = SimpleNamespace(delay=Mock(side_effect=ConnectionError("broker unavailable")))
    db = SimpleNamespace(commit=Mock())

    warning = _dispatch_job(db, job, task)

    assert warning == "任务队列暂不可用，任务已记录为失败，可在任务列表中重试"
    assert job.status == "failed"
    assert job.error_code == "QUEUE_DISPATCH_FAILED"
    assert "broker unavailable" in job.error_message
    assert job.finished_at is not None
    db.commit.assert_called_once()


def test_successful_queue_publish_does_not_rewrite_job() -> None:
    job = SimpleNamespace(
        id="job-2",
        job_type="process_knowledge",
        input={},
        status="queued",
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    task = SimpleNamespace(delay=Mock())
    db = SimpleNamespace(commit=Mock())

    assert _dispatch_job(db, job, task) is None
    task.delay.assert_called_once_with("job-2")
    db.commit.assert_not_called()


def test_upload_ui_surfaces_queue_warning() -> None:
    app = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/api/static/app.js"
    ).read_text(encoding="utf-8")

    assert "result?.warning||'已提交解析'" in app


def test_database_snapshot_mode_can_skip_automatic_knowledge_processing() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")

    assert 'get("knowledge_index_enabled") is False' in worker
    assert "settings.knowledge_auto_process and source_knowledge_enabled" in worker


def test_cancelled_or_deleted_knowledge_jobs_do_not_continue() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")

    assert 'if job.status == "cancelled"' in worker
    assert "version.deleted_at is not None" in worker
    assert "document.deleted_at is not None" in worker


def test_model_extraction_timeout_degrades_without_losing_search_index() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")

    assert "extraction_errors.append" in worker
    assert 'run.status = "partial" if extraction_errors else "succeeded"' in worker
    assert '"semantic_extraction_failed_chunks": len(extraction_errors)' in worker
    assert 'job.result["warnings"]' in worker


def test_document_cannot_be_deleted_during_knowledge_processing() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(code="job-delete", name="任务删除边界")
        db.add(tenant)
        db.flush()
        admin = User(
            tenant_id=tenant.id,
            username="admin",
            password_hash="unused",
            display_name="管理员",
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        space = KnowledgeSpace(
            tenant_id=tenant.id,
            code="processing",
            name="加工中空间",
            owner_id=admin.id,
        )
        db.add(space)
        db.flush()
        document = Document(
            tenant_id=tenant.id,
            space_id=space.id,
            title="加工中文档.pdf",
            owner_id=admin.id,
            status="processing",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            tenant_id=tenant.id,
            document_id=document.id,
            version_number=1,
            filename="加工中文档.pdf",
            content_type="application/pdf",
            size=10,
            sha256="a" * 64,
            object_key="test/processing.pdf",
            status="ready",
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        db.add(Job(
            tenant_id=tenant.id,
            job_type="process_knowledge",
            status="running",
            idempotency_key="knowledge:delete-boundary",
            input={"version_id": version.id},
        ))
        db.commit()

        with pytest.raises(HTTPException) as error:
            delete_document(document.id, admin, db)

        assert error.value.status_code == 409
        assert "正在解析或加工" in error.value.detail
        assert document.deleted_at is None
