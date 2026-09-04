from pathlib import Path


ROOT = Path(__file__).parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_configuration_center_exposes_model_routing_crud() -> None:
    assert "modelrouting:{title:'模型路由'" in APP
    assert "['modelrouting','模型路由']" in APP
    assert "renderModelRoutingPolicies" in APP
    assert "'/model-routing-policies'" in APP
    assert "新增路由策略" in APP
    assert "model-routing-edit" in APP
    assert "model-routing-delete" in APP
    assert "function modelRoutingForm(x={},models=[]){x=x||{}" in APP


def test_all_supported_model_scenes_are_visible_and_form_is_responsive() -> None:
    for label in (
        "智能问答", "图谱语义抽取", "文档治理画像", "结构化查询规划",
        "视觉理解", "向量化", "检索重排", "语音识别",
    ):
        assert label in APP
    assert "跟随模型路由" in APP
    assert ".model-routing-form" in STYLE
