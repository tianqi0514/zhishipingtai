from types import SimpleNamespace
from pathlib import Path

from apps.api.routes import _unresolved_failed_jobs


def _job(job_id: str, status: str, *, version_id: str | None = None, job_type: str = "process_knowledge"):
    return SimpleNamespace(
        id=job_id,
        status=status,
        job_type=job_type,
        input={"version_id": version_id} if version_id else {},
    )


def test_dashboard_failure_projection_ignores_a_failed_attempt_after_successful_retry() -> None:
    # Rows are newest-first, as returned by the jobs/dashboard queries.
    rows = [
        _job("retry-ok", "succeeded", version_id="version-a"),
        _job("original-failure", "failed", version_id="version-a"),
        _job("still-failed", "failed", version_id="version-b"),
    ]

    unresolved = _unresolved_failed_jobs(rows)

    assert [row.id for row in unresolved] == ["still-failed"]


def test_dashboard_failure_projection_keeps_independent_targets_and_partial_failures() -> None:
    rows = [
        _job("source-partial", "partial_failed", version_id=None, job_type="sync_source"),
        _job("doc-failed", "failed", version_id="version-a"),
        _job("other-doc-ok", "succeeded", version_id="version-b"),
    ]

    assert {row.id for row in _unresolved_failed_jobs(rows)} == {
        "source-partial",
        "doc-failed",
    }


def test_dashboard_failure_projection_ignores_obsolete_document_versions() -> None:
    rows = [
        _job("old-version-failed", "failed", version_id="version-old"),
        _job("current-version-ok", "succeeded", version_id="version-current"),
    ]

    assert _unresolved_failed_jobs(
        rows,
        current_version_ids={"version-current"},
    ) == []


def test_frontend_summary_cards_use_the_same_unresolved_failure_projection() -> None:
    app_js = (Path(__file__).parents[2] / "apps/api/static/app.js").read_text(encoding="utf-8")

    assert "function unresolvedFailedJobs(rows=[])" in app_js
    assert "row.target_current!==false" in app_js
    assert "异常任务</span><b>${unresolvedFailedJobs(jobs).length}" in app_js
    assert "失败任务</span><b>${unresolvedFailedJobs(jobs).length}" in app_js
    assert "knowledge_inference:'知识分析'" in app_js
    assert "curation_publish:'治理发布'" in app_js
