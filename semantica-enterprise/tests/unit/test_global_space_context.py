from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "apps/api/static/structured-workbench.js").read_text(encoding="utf-8")
MCP_GUIDE = (ROOT / "apps/api/static/manuals/mcp.md").read_text(encoding="utf-8")
ROUTES = (ROOT / "apps/api/routes.py").read_text(encoding="utf-8")


def test_header_has_one_prominent_persistent_knowledge_space_context() -> None:
    assert 'id="space-context"' in INDEX
    assert 'id="global-space-select"' in INDEX
    assert "chuanshen.activeSpace.${state.user?.id||'guest'}" in APP
    assert "function switchActiveSpace" in APP
    assert ".space-context.scoped" in STYLE
    assert ".space-context:not(.scoped){display:none}" in STYLE


def test_business_modules_follow_the_current_space() -> None:
    for endpoint in ("/dashboard", "/documents", "/sources", "/jobs"):
        assert f"currentSpaceQuery('{endpoint}')" in APP
    assert "const spaceId=currentSpaceId();state.curationSpaceId=spaceId" in APP
    assert "actions();await refreshLookups();const spaceId=currentSpaceId()" in APP
    assert "spaceIds=selectedChatSpaceIds()" in APP
    assert "space_ids:[spaceId]" in APP
    assert "spaceBound(await api('/analysis/rule-sets'))" in APP
    assert "spaceBound(await api('/analysis/scenarios'))" in APP
    assert "spaceBound(await api('/analysis/inference-runs'))" in APP
    assert "spaceBound(await api('/analysis/saved-queries'))" in APP


def test_space_scoped_dashboard_and_jobs_are_enforced_server_side() -> None:
    assert "def dashboard(" in ROUTES
    assert "space_id: str | None = None" in ROUTES
    assert "space_id in _job_space_ids(db, row)" in ROUTES
    assert "def _job_space_ids" in ROUTES
    assert 'require_space_permission(db, user, space_id, "read")' in ROUTES


def test_frontend_replaces_vendor_terms_with_semantic_modeling_language() -> None:
    public_sources = "\n".join((APP, WORKBENCH, MCP_GUIDE))
    assert "Semantica" not in public_sources
    assert "Semantic Query Plan" not in public_sources
    assert "语义建模" in public_sources
