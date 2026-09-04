from pathlib import Path


ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_primary_navigation_uses_nine_business_domains() -> None:
    assert INDEX.count('class="nav-group') == 9
    for label in (
        "工作台",
        "知识资产",
        "数据接入",
        "知识治理",
        "知识服务",
        "运营中心",
        "应用构建",
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
    for view in ("assets", "retrieval", "integrations", "operations", "applications", "products", "appscenarios", "evaluations", "appaccess", "feedback", "configuration", "system"):
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
    assert "dlg.addEventListener('close',()=>resolve(value),{once:true})" in APP


def test_platform_permissions_drive_application_and_audit_navigation() -> None:
    assert "permission:'application.manage'" in APP
    assert "permission:'audit.read'" in APP
    assert "function hasPlatformPermission" in APP
    assert "function canView" in APP
    assert "adminOnly:true" in APP
    assert "state.user.is_admin?cachedApi('/users',{force}):Promise.resolve([state.user])" in APP


def test_governance_consolidates_content_graph_reasoning_and_releases() -> None:
    governance = INDEX.split('data-nav-group="governance"', 1)[1].split(
        'data-nav-group="service"', 1
    )[0]
    for label in ("治理概览", "内容治理", "知识图谱", "规则推演", "发布记录"):
        assert label in governance
    assert 'data-nav-group="insights"' not in INDEX
    assert "governance:[['governance','治理概览']" in APP


def test_navigation_has_abortable_request_lifecycle_and_scoped_cache() -> None:
    assert "state.viewAbort=new AbortController()" in APP
    assert "if(method==='GET'&&!detached&&!init.signal&&state.viewAbort)" in APP
    assert "function cachedApi" in APP
    assert "function invalidateAfterMutation" in APP
    assert "chuanshen.activeSpace.${state.user?.id||'guest'}" in APP
    assert "window.__chuanshenDiagnostics" in APP
    assert "duplicate_request_count" in APP


def test_background_jobs_use_business_labels() -> None:
    assert "process_knowledge:'知识加工'" in APP
    assert "sync_source:'数据源同步'" in APP
    assert "jobTypeLabel(x.job_type)" in APP


def test_dashboard_has_one_state_driven_recommended_next_step() -> None:
    assert "建议下一步" in APP
    assert "dashboard-next-step" in APP
    assert "完成模型配置" in APP
    assert "接入第一批真实知识" in APP


def test_audit_log_uses_business_labels_and_hides_raw_identifiers_by_default() -> None:
    render_audits = APP.split("async function renderAudits()", 1)[1].split(
        "window.addEventListener", 1
    )[0]
    assert "业务操作" in render_audits
    assert "业务对象" in render_audits
    assert "auditActionLabel" in render_audits
    assert "auditActorLabel" in render_audits
    assert "data-audit-detail" in render_audits
    assert "JSON.stringify(x.detail" not in render_audits
    assert "对象 ID" not in render_audits
