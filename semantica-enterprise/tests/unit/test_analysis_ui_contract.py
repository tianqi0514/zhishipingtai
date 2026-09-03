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
    assert "rollback-preview" in javascript


def test_four_analysis_features_are_business_explained_and_semantic_modeling_backed() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")

    for feature, label in (
        ("scenarios", "场景分析"),
        ("rules", "规则中心"),
        ("results", "推理结果"),
        ("query", "高级查询"),
    ):
        assert f"{feature}:{{title:'{label}'" in javascript
    assert "showAnalysisFeature" in javascript
    assert "语义建模如何参与" in javascript
    assert "Semantica 如何参与" not in javascript
    assert "analysisRuleNatural" in javascript
    assert "'/analysis/rules/validate'" in javascript
    assert "ANALYSIS_QUERY_EXAMPLES" in javascript
    assert "导出 CSV" in javascript
    assert "scenario-grid" in javascript
    assert "analysis-rule-layout" in javascript
    assert "analysis-result-layout" in javascript
    assert "form.elements.namedItem" in javascript
    assert "已删除规则集" in javascript
    assert "state.analysisSelectedRunId=submittedRun?.id" in javascript
