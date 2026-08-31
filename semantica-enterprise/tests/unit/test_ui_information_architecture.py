from pathlib import Path


ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_primary_navigation_uses_eight_business_domains() -> None:
    assert INDEX.count('class="nav-group') == 8
    for label in (
        "工作台",
        "知识资产",
        "数据接入",
        "知识服务",
        "知识洞察",
        "运营中心",
        "配置中心",
        "系统管理",
    ):
        assert f">{label}<" in INDEX


def test_configuration_and_system_features_are_grouped() -> None:
    configuration = INDEX.split('data-nav-group="configuration"', 1)[1].split(
        'data-nav-group="system"', 1
    )[0]
    for label in ("模型服务", "解析策略", "加工策略", "知识结构"):
        assert label in configuration
    system = INDEX.split('data-nav-group="system"', 1)[1]
    for label in ("组织机构", "用户管理", "角色权限", "审计日志"):
        assert label in system


def test_business_routes_support_history_and_context_tabs() -> None:
    for view in ("assets", "retrieval", "integrations", "operations", "configuration", "system"):
        assert f"{view}:" in APP
    assert "history.pushState" in APP
    assert "window.addEventListener('popstate'" in APP
    assert "moduleTabs()" in APP


def test_light_theme_overrides_graph_and_scroll_layout() -> None:
    enterprise_marker = STYLE.index("Enterprise information architecture")
    enterprise_style = STYLE[enterprise_marker:]
    assert "--bg:#f5f7fa" in enterprise_style
    assert "--brand:#2f6bff" in enterprise_style
    assert ".graph-stage{background:" in enterprise_style
    assert ",#fff}" in enterprise_style
    assert "#content>.module-tabs+.view-body{grid-row:2;overflow:auto}" in enterprise_style
    assert ".content-view.chat-view>.view-body{overflow:hidden}" in enterprise_style


def test_shared_modal_resets_submit_state_between_crud_operations() -> None:
    assert "submitButton.textContent=submit;submitButton.disabled=false" in APP
    assert "finally{submitButton.disabled=false" in APP
