from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")
ROUTES = (ROOT / "apps/api/routes.py").read_text(encoding="utf-8")


def test_new_space_enters_its_document_list_with_a_real_next_action() -> None:
    assert "state.activeSpaceId=saved.id" in APP
    assert "state.pendingSpaceCreatedId=saved.id" in APP
    assert "await go('documents',true,{space_created:saved.id,_skipCapture:true})" in APP
    assert "知识空间已创建，可以上传第一份文档" in APP
    assert "这个知识空间还没有文档" in APP
    assert "data-doc-upload-mode" in APP


def test_document_upload_modes_lead_to_real_processing() -> None:
    assert "uploadDoc(doc,defaultMode=null)" in APP
    assert "knowledgeProcessingField(defaultMode||effectiveKnowledgeProcessingMode" in APP
    assert "fd.set('knowledge_processing_mode'" in APP
    assert "go('jobs')" in APP


def test_completed_document_offers_mode_specific_real_next_steps() -> None:
    assert "function documentJourneyNext" in APP
    assert "检索索引和知识图谱已生成" in APP
    assert 'data-doc-next="knowledge"' in APP
    assert 'data-doc-next="search"' in APP
    assert "该文档没有进入检索索引" in APP
    assert "该文档没有进行图谱加工" in APP


def test_governance_overview_uses_real_backend_projection_state() -> None:
    assert '@router.get("/knowledge/governance-overview")' in ROUTES
    for field in (
        "document_count",
        "parsed_document_count",
        "processing_modes",
        "open_curation_cases",
        "entity_count",
        "asserted_fact_count",
        "inferred_fact_count",
        "current_graph_release",
        "current_index_release",
        "current_knowledge_release",
        "next_action",
    ):
        assert f'"{field}"' in ROUTES
    assert "renderGovernanceOverview" in APP
    assert "知识加工与治理流程" in APP
    assert "该文档未进行图谱加工" not in APP  # counts, not fabricated per-document labels
    assert "份仅生成检索索引，未进行图谱加工" in APP
    assert "份仅生成知识图谱，未进入检索索引" in APP


def test_graph_empty_state_explains_projection_requirements() -> None:
    assert "当前空间还没有知识图谱" in APP
    assert "上传并选择“仅图谱”" in APP
    assert "上传并选择“检索 + 图谱”" in APP
    assert "对现有文档补充图谱加工" in APP
    assert "data-graph-view=\"jobs\"" in APP


def test_release_records_are_not_a_static_demo() -> None:
    assert "async function renderReleaseRecords" in APP
    assert "`/knowledge/releases?space_id=${encodeURIComponent(space.id)}`" in APP
    assert "releases.knowledge" in APP
    assert "releases.graphs" in APP
    assert "releases.indexes" in APP


def test_business_journey_styles_are_responsive() -> None:
    for selector in (
        ".journey-success",
        ".journey-empty",
        ".governance-stage-list",
        ".governance-next",
        ".release-timeline",
        ".graph-readiness",
    ):
        assert selector in STYLE
    assert "@media(max-width:1320px)" in STYLE
