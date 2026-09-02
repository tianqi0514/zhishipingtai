from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "apps/api/static/structured-workbench.js").read_text(encoding="utf-8")
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_database_workbench_is_loaded_and_uses_real_endpoints() -> None:
    assert "structured-workbench.js" in INDEX
    for endpoint in (
        "/schema/discover",
        "/schema/versions",
        "/data-preview",
        "/preview/config",
        "/semantic-mappings",
        "/structured-query/natural-language",
    ):
        assert endpoint in WORKBENCH
    assert "实时数据库" in WORKBENCH
    assert "最近同步快照" in WORKBENCH
    assert "结构发现" in WORKBENCH
    assert "语义映射" in WORKBENCH
    for action in (
        "add-mapped-entity",
        "add-mapped-attribute",
        "add-mapped-relationship",
        "dbDeleteEntity",
        "dbDeleteAttribute",
        "dbDeleteRelationship",
    ):
        assert action in WORKBENCH


def test_chat_renders_real_structured_events_and_data_citations() -> None:
    for event_type in (
        "structured_schema_search_started",
        "structured_schema_search_finished",
        "structured_plan_started",
        "structured_plan_validated",
        "structured_ir_validated",
        "structured_query_compiled",
        "structured_query_started",
        "structured_query_finished",
        "structured_query_failed",
        "structured_query_cancelled",
    ):
        assert event_type in APP
    assert "data-structured-citation" in APP
    assert "data-open-query-run" in APP
    assert "/structured-query/runs/" in APP
    assert ".inspector-evidence-card.structured" in STYLE


def test_chat_space_scope_uses_visible_persistent_toggle_buttons() -> None:
    assert 'data-chat-space="${x.id}"' in APP
    assert "selectedChatSpaceIds" in APP
    assert "persistChatSettings" in APP
    assert "toggleChatSpace" in APP
    assert "至少选择一个知识空间" in APP
    assert ".chat-space-chip.selected" in STYLE
    assert ".chat-setting-body{display:grid" in STYLE


def test_database_workbench_prevents_page_level_wide_table_overflow() -> None:
    assert "overflow-x:auto" in STYLE
    assert ".db-data-grid" in STYLE
    assert "width:max-content" in STYLE
    assert "position:sticky" in STYLE
    assert ".db-source-tabs button{width:auto" in STYLE


def test_unknown_database_row_estimates_are_not_presented_as_empty_tables() -> None:
    assert "dbRowEstimateLabel" in WORKBENCH
    assert "行数待统计" in WORKBENCH
    assert "总行数待统计" in WORKBENCH


def test_metric_contract_is_visible_and_editable_in_the_mapping_workbench() -> None:
    assert "业务统计口径" in WORKBENCH
    assert "固定口径筛选" in WORKBENCH
    assert "default_aggregate" in WORKBENCH
    assert "required_filters" in WORKBENCH


def test_running_message_and_inspector_share_the_real_turn_timer() -> None:
    assert "process.textContent=`正在执行 · ${label}`" in APP
    assert "chatTurnTiming(message)" in APP
