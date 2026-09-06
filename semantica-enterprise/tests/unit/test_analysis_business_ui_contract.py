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
    assert "#analysis-body:empty" in stylesheet
    assert "state.viewAbort?.abort()" in javascript
    assert "state.viewAbort=new AbortController()" in javascript


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


def test_visual_graph_query_reads_rendered_fields_without_stale_dom_crashes() -> None:
    """Query handlers must tolerate navigation replacing their rendered controls."""

    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")

    for selector in (
        "subject=$('[name=visual_subject]')",
        "predicate=$('[name=visual_predicate]')",
        "objectType=$('[name=visual_object_type]')",
        "inferred=$('[name=visual_inferred]')",
    ):
        assert selector in javascript
    assert "if(!subject||!predicate||!objectType||!inferred)" in javascript
    assert "viewSequence!==state.requestSequence" in javascript
    assert "renderToken!==state.analysisQueryRenderToken" in javascript
    assert "if(button.isConnected)" in javascript
    assert "if(err?.name==='AbortError'||!isCurrent())return" in javascript
    assert "$('#visual_subject').value" not in javascript
    assert "$('#visual_predicate').value" not in javascript
    assert "$('#visual_object_type').value" not in javascript


def test_analysis_dynamic_forms_use_controlled_missing_field_errors() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")

    assert "function analysisRequiredControl" in javascript
    assert "function analysisRequiredNamedControl" in javascript
    assert "function analysisControlValue" in javascript
    assert "function analysisNamedValue" in javascript
    assert "已更新，请关闭后重新打开表单" in javascript
    assert "row.querySelector('select').value.trim()" not in javascript
    assert "form.elements.head_predicate.value" not in javascript
    assert "form.name.value" not in javascript
    assert "form.priority.value" not in javascript
    assert "form.confidence.value" not in javascript

    await_index = javascript.rindex("const queryResult=await api(path")
    freshness_index = javascript.index("if(!isCurrent())return", await_index)
    assignment_index = javascript.index("state.analysisQueryResult=queryResult", await_index)
    assert await_index < freshness_index < assignment_index
