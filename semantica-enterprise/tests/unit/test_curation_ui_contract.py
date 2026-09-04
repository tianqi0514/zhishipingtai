from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_curation_is_a_governance_submodule_and_not_a_top_level_module() -> None:
    html = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    assert '<button data-view="curation">内容治理</button>' in html
    assert "curation:{title:'内容治理',group:'governance'}" in javascript
    assert "async function renderCuration()" in javascript


def test_p0_to_p3_curation_controls_call_real_apis() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    for endpoint in (
        "`/curation/workbench?${params}`",
        "`/curation/batches?space_id=${encodeURIComponent(spaceId)}&limit=300`",
        "`/curation/profiles/${versionId}`",
        "'/curation/decisions'",
        "'/curation/entities/pair'",
        "`/curation/batches/${batch.id}/rollback`",
    ):
        assert endpoint in javascript
    for label in ("修正文档画像", "修正原始内容", "调整召回优先级", "实体合并与拆分"):
        assert label in javascript


def test_curation_workbench_has_effective_source_and_rollback_language() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    for label in ("待处理", "人工调整", "发布记录", "系统生成", "当前生效", "查找知识并治理"):
        assert label in javascript
    assert "回滚本批次" in javascript
    assert "不会删除历史自动结果" in javascript


def test_profile_curation_uses_business_inputs_instead_of_raw_json() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    profile_section = javascript.split("async function saveProfileCuration", 1)[1].split("async function editContentElement", 1)[0]
    assert "tokenField('tags'" in profile_section
    assert "tokenField('keywords'" in profile_section
    assert "tokenField('main_objects'" in profile_section
    assert "field('time_start'" in profile_section
    assert "field('time_end'" in profile_section
    assert "JSON.stringify({automatic" not in profile_section
