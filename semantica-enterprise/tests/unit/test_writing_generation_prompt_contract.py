import json

from packages.platform.writing_flow import build_generation_prompt


def test_generation_carries_per_chapter_evidence_and_computation_contract():
    section = {"key": "resources", "title": "人员保障", "generation_mode": "mixed", "instruction": "据清单核验。", "citation_required": True, "required_inputs": ["rescue_required", "rescue_available"], "toolbox_outputs": ["rescue_gap"]}
    prompt = build_generation_prompt(project_name="虚构测试", section_plan=[section])
    serialized = prompt.split("章节契约：", 1)[1].split("\n输出结构示例：", 1)[0]
    contract = json.loads(serialized)[0]
    for field in ("citation_required", "required_inputs", "toolbox_outputs"):
        assert contract[field] == section[field]
    assert "不能只填 citation_refs 数组" in prompt
    assert "不把差值、比例或规则结论说成原文件直接记载" in prompt
    example = json.loads(prompt.split("输出结构示例：", 1)[1])
    assert example["sections"][0]["content_nodes"][0]["input_refs"] == []
    assert example["sections"][0]["content_nodes"][0]["metric_refs"] == []


def test_generation_keeps_optional_citations_optional():
    prompt = build_generation_prompt(project_name="虚构测试", section_plan=[{"key": "note", "title": "说明", "citation_required": False}])
    contract = json.loads(prompt.split("章节契约：", 1)[1].split("\n输出结构示例：", 1)[0])
    assert contract[0]["citation_required"] is False
    assert contract[0]["required_inputs"] == []
    assert contract[0]["toolbox_outputs"] == []
