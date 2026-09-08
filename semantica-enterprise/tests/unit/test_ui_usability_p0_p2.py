from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def nav_group(name: str, next_name: str) -> str:
    return INDEX.split(f'data-nav-group="{name}"', 1)[1].split(
        f'data-nav-group="{next_name}"', 1
    )[0]


def test_secondary_tools_are_removed_from_primary_navigation() -> None:
    service = nav_group("service", "applications")
    operations = nav_group("operations", "configuration")
    applications = nav_group("applications", "operations")
    assert "检索诊断" not in service
    assert "能力检测" not in operations
    assert applications.count('data-view="applications"') == 2  # primary icon and one child entry
    assert "operations-retrieval" in APP
    assert 'data-operations-view="capabilities"' in APP


def test_space_scoped_pages_have_one_real_prerequisite_state() -> None:
    assert "function spaceRequiredState" in APP
    for renderer in (
        "renderDocuments",
        "renderSources",
        "renderCuration",
        "renderKnowledge",
        "renderRetrievalDebug",
        "renderSearch",
        "renderAnalysis",
    ):
        section = APP.rsplit(f"async function {renderer}()", 1)[1][:5000] if renderer in {"renderSources", "renderCuration", "renderRetrievalDebug", "renderSearch", "renderAnalysis"} else APP.split(f"async function {renderer}", 1)[1][:5000]
        assert "spaceRequiredState" in section
    assert "data-empty-action=\"create-space\"" in APP
    assert "if(state.view==='spaces')editSpace();else go('spaces').then(()=>editSpace())" in APP


def test_empty_graph_has_only_clear_business_actions() -> None:
    section = APP.split("async function renderKnowledge(selected)", 1)[1].split(
        "function answerHtml", 1
    )[0]
    assert "上传并生成图谱" in section
    assert "手工新增节点" in section
    assert "上传并选择“仅图谱”" not in section
    assert "对现有文档补充图谱加工" not in section


def test_model_errors_are_sanitized_and_model_terms_are_localized() -> None:
    assert "function userFacingError" in APP
    assert "认证失败（API Key 无效）" in APP
    assert "MODEL_KIND_LABELS[x.model_kind]" in APP
    assert "MODEL_PROVIDER_LABELS[x.provider]" in APP
    render_models = APP.split("async function renderModels()", 1)[1].split(
        "const MODEL_ROUTE_LABELS", 1
    )[0]
    assert "userFacingError(x.last_test_message" in render_models


def test_complex_configuration_uses_progressive_disclosure() -> None:
    assert "function advancedSection" in APP
    assert "collapseMediaPolicyGroups" in APP
    assert "advancedArea('config','服务专用参数'" in APP
    assert "advancedSection('技术信息','命名空间和扩展参数'" in APP
    assert ".advanced-config-section" in STYLE


def test_roles_use_permission_matrix_instead_of_raw_comma_input() -> None:
    section = APP.split("const ROLE_PERMISSION_GROUPS", 1)[1].split(
        "async function renderUsers", 1
    )[0]
    assert "permission-matrix" in section
    assert "role_permission" in section
    assert "平台全部权限" in section
    assert "<span>权限（逗号分隔）</span>" not in section
    assert ".permission-option" in STYLE


def test_evaluation_cases_select_real_evidence_chunks() -> None:
    assert "function evidencePicker" in APP
    assert "function bindEvidencePicker" in APP
    assert "target_type=chunk" in APP
    assert "data-evidence-add" in APP
    assert "标准依据片段 ID" not in APP
    assert ".evidence-picker" in STYLE


def test_application_steps_are_gated_by_real_prerequisites() -> None:
    assert "需要先准备知识空间" in APP
    assert "需要先发布知识供给" in APP
    assert "需要先发布能力场景" in APP
    assert "需要先创建业务应用" in APP
    assert "requirementState" in APP


def test_presentation_copy_is_not_persistently_shown() -> None:
    for phrase in (
        "当前页面及下级模块统一使用此知识范围",
        "版本、解析、治理和溯源集中管理",
        "点击任一步骤继续处理",
        "当前文档加工方式与发布状态已同步",
    ):
        assert phrase not in APP
