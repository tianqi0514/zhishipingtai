from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_knowledge_analysis_ui_has_real_crud_and_execution_routes() -> None:
    html = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")

    assert 'data-view="analysis"' in html
    assert "async function renderAnalysis()" in javascript
    for endpoint in (
        "/analysis/rule-sets",
        "/analysis/scenarios",
        "/analysis/inference-runs",
        "/analysis/saved-queries",
        "/analysis/sparql",
    ):
        assert endpoint in javascript
    assert "data-condition-remove" in javascript
    assert "run-rollback" in javascript
