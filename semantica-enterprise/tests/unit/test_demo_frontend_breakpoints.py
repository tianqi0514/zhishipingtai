from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "apps/api/static/structured-workbench.js").read_text(encoding="utf-8")
MAIN = (ROOT / "apps/api/main.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "packages/platform/config.py").read_text(encoding="utf-8")


def test_default_analysis_task_help_has_business_content() -> None:
    assert "tasks:{title:'分析任务'" in APP
    assert "从一个业务问题出发" in APP
    assert 'data-analysis-help="${state.analysisTab}"' in APP


def test_structured_query_cancel_reaches_backend_when_run_id_is_available() -> None:
    assert "function activeStructuredQueryRunId" in WORKBENCH
    assert "async function cancelNaturalDatabaseQuery" in WORKBENCH
    assert "/structured-query/${encodeURIComponent(runId)}/cancel" in WORKBENCH
    assert "dbw.queryAbort?.abort()" in WORKBENCH


def test_preview_policy_exposes_complete_backend_contract_and_exact_count() -> None:
    for field in (
        "allowed_columns",
        "sensitive_columns",
        "masking_rules",
        "default_order",
        "max_filter_conditions",
        "max_result_bytes",
    ):
        assert field in WORKBENCH
    assert "字段访问与脱敏" in WORKBENCH
    assert "脱敏在服务端执行" in WORKBENCH
    assert "body:{...policy,...d" not in WORKBENCH
    assert "body:{...draft,...d" not in WORKBENCH
    assert "/data-preview/count" in WORKBENCH
    assert "精确 COUNT" in WORKBENCH


def test_open_service_addresses_follow_browser_origin_and_deploy_config() -> None:
    integrations = APP.split("async function renderIntegrations()", 1)[1].split(
        "async function renderSearch()", 1
    )[0]
    assert "window.location.origin" in APP
    assert "__CHUANSHEN_PUBLIC_ENDPOINTS__" in APP
    assert "http://localhost:8080" not in integrations
    assert "http://localhost:8091" not in integrations
    assert "/public-config" in APP
    assert "public_config" in MAIN
    assert "mcp_public_url" in CONFIG


def test_inferred_graph_edge_opens_exact_analysis_run_and_fact() -> None:
    inspector = APP.split("function renderGraphInspector", 1)[1].split(
        "async function renderKnowledge", 1
    )[0]
    assert "state.analysisSelectedRunId=item.run_id" in inspector
    assert "state.analysisSelectedFactId=item.id" in inspector
    assert "state.analysisTab='results'" in inspector
