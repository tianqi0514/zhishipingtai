from pathlib import Path


ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_application_builder_navigation_exposes_one_business_entry() -> None:
    group = INDEX.split('data-nav-group="applications"', 1)[1].split(
        'data-nav-group="configuration"', 1
    )[0]
    assert "应用工作台" in group
    for label in ("知识供给", "能力场景", "上线测试", "接入发布", "运行反馈"):
        assert label not in group
        assert f"title:'{label}'" in APP


def test_application_workbench_is_a_simple_writing_entry() -> None:
    for phrase in (
        "新建写作应用",
        "写作知识范围",
        "进入妙笔",
        "生成、改写、引用、导出",
        "打开妙笔编辑器",
    ):
        assert phrase in APP
    assert "renderApplicationWorkbench" in APP


def test_application_creation_exposes_all_authorized_spaces_and_supply_modes() -> None:
    for phrase in (
        "写作时使用哪些知识？",
        "尚无已发布知识，请先完成文档加工",
        "创建并开始写作",
        "/applications/guided",
    ):
        assert phrase in APP
    assert "state.spaces.map" in APP
    assert "/knowledge/releases?space_id=" in APP
    assert "v=20260914-writing-app-simple" in INDEX


def test_application_builder_keeps_technical_details_on_demand() -> None:
    assert "功能说明" in APP
    assert "技术选项" in APP
    assert "查看接入说明" in APP
    assert "Secret 只在生成或轮换时显示一次" in APP


def test_service_secret_ui_makes_one_time_display_explicit() -> None:
    assert "密钥仅显示一次" in APP
    assert "showCredentialSecret" in APP
    assert "client_secret" in APP
    assert "secret_hash" not in APP
    assert "navigator.clipboard.writeText" in APP


def test_product_release_and_scenario_version_actions_call_real_apis() -> None:
    for route in (
        "/knowledge-products/${selected.id}/releases",
        "/knowledge-products/${product.id}/aliases/${alias}",
        "/application-scenarios/${scenario.id}/versions",
        "/evaluation-runs",
        "/convert-to-curation",
    ):
        assert route in APP


def test_application_feedback_closes_through_governance_workbench() -> None:
    assert "通过的上线测试" in APP
    assert "查看治理任务" in APP
    assert "runtime-feedback-curation" in APP
    assert "runtime-feedback-verify" in APP
    assert "/verify-resolution" in APP


def test_knowledge_supply_warns_when_upstream_release_changes() -> None:
    assert "release_freshness" in APP
    assert "上游知识已更新" in APP
    assert "supply-update-release" in APP


def test_foundation_layout_has_bounded_master_detail_responsiveness() -> None:
    assert ".foundation-layout" in STYLE
    assert "max-height:calc(100vh - 155px)" in STYLE
    assert "@media(max-width:760px){.foundation-layout{grid-template-columns:1fr}" in STYLE
    assert ".application-context-bar" in STYLE
    assert ".application-journey" in STYLE
