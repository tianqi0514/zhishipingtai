from pathlib import Path


ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_application_foundation_navigation_exposes_five_business_modules() -> None:
    group = INDEX.split('data-nav-group="applications"', 1)[1].split(
        'data-nav-group="configuration"', 1
    )[0]
    for label in ("应用中心", "知识产品", "场景配置", "质量评测", "反馈中心"):
        assert label in group


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


def test_foundation_layout_has_bounded_master_detail_responsiveness() -> None:
    assert ".foundation-layout" in STYLE
    assert "max-height:calc(100vh - 155px)" in STYLE
    assert "@media(max-width:760px){.foundation-layout{grid-template-columns:1fr}" in STYLE
