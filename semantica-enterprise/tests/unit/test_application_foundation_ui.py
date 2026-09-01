from pathlib import Path


ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_application_builder_navigation_exposes_six_journey_steps() -> None:
    group = INDEX.split('data-nav-group="applications"', 1)[1].split(
        'data-nav-group="configuration"', 1
    )[0]
    for label in ("应用工作台", "知识供给", "能力场景", "上线测试", "接入发布", "运行反馈"):
        assert label in group


def test_application_workbench_uses_business_journey_and_readiness() -> None:
    for phrase in (
        "应用上线流程",
        "上线准备度",
        "选择知识供给",
        "选择能力场景",
        "完成上线测试",
        "发布接入",
    ):
        assert phrase in APP
    assert "applicationReadiness" in APP
    assert "renderApplicationWorkbench" in APP
    assert "renderApplicationAccess" in APP


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
    assert "通过门禁的上线测试确认" in APP
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
    assert ".application-flow-tabs" in STYLE
    assert ".application-journey" in STYLE
