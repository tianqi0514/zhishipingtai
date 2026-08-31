from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_curation_is_an_asset_submodule_and_not_a_top_level_module() -> None:
    html = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    assert '<button data-view="curation">治理工作台</button>' in html
    assert "curation:{title:'治理工作台',group:'assets'}" in javascript
    assert "async function renderCuration()" in javascript


def test_p0_to_p3_curation_controls_call_real_apis() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    for endpoint in (
        "'/curation/batches'",
        "'/curation/decisions'",
        "'/curation/entities/pair'",
        "`/curation/decisions/${b.dataset.id}/rollback`",
    ):
        assert endpoint in javascript
    for label in ("人工修正", "修正内容元素", "调整检索权重", "实体合并与拆分"):
        assert label in javascript


def test_curation_workbench_has_effective_source_and_rollback_language() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    assert "自动结果不变，生效值由约束层合成" in javascript
    assert "查看自动结果与生效来源" in javascript
    assert "回滚该项人工治理决定" in javascript
