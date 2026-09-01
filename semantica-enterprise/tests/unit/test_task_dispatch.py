from types import SimpleNamespace
from unittest.mock import Mock

from apps.api.routes import _dispatch_job


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
