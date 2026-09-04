from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_analysis_ui_starts_from_business_task_and_has_guided_flow() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")

    assert "analysisTab:'tasks'" in javascript
    for label in ("分析任务", "发现结果", "规则库", "专业查询"):
        assert label in javascript
    for endpoint in (
        "/analysis/readiness",
        "/analysis/vocabulary",
        "/analysis/templates",
        "/analysis/rules/match-preview",
        "/analysis/guided-setups",
        "/analysis/tasks",
        "/analysis/visual-query",
    ):
        assert endpoint in javascript
    assert "openAnalysisWizard" in javascript
    assert "结论中的对象必须已经出现在判断条件中" in javascript
    assert "当前空间的关系" in javascript
    assert "不由大模型自由生成" in javascript
    assert ".analysis-business-tabs" in stylesheet
    assert ".analysis-wizard" in stylesheet
    assert ".zero-diagnostics" in stylesheet


def test_analysis_ui_uses_business_terms_and_preserves_expert_query() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")

    assert "加入知识库 · 影响预览" in javascript
    assert "撤回本次发布" in javascript
    assert "本次新发现" in javascript
    assert "仍然成立" in javascript
    assert "已失效" in javascript
    assert "普通查询" in javascript
    assert "SPARQL 编辑器" in javascript
    assert "run_readonly_sparql" not in javascript
