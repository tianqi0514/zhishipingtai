from pathlib import Path


APP = (Path(__file__).parents[2] / "apps/api/static/app.js").read_text(encoding="utf-8")


def test_failed_job_step_uses_backend_error_instead_of_completed_fallback() -> None:
    assert "if(step.status==='failed')" in APP
    assert "d.message||d.error_message||d.error||'本步骤执行失败'" in APP
    assert '<span class="error-inline">${esc(message)}</span>' in APP


def test_incomplete_job_steps_have_explicit_progress_labels() -> None:
    assert "if(step.status==='running')return `<span>正在处理</span>" in APP
    assert "if(step.status==='queued')return '<span class=\"muted\">等待执行</span>'" in APP
    assert "if(step.status==='cancelled')return `<span class=\"step-skip\">已取消</span>" in APP
