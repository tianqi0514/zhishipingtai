import json

import pytest

from packages.platform.writing_flow import (
    build_generation_prompt, normalize_agent_heading_refs, renumber_chapter_citations,
    report_quality_review, validate_and_parse_agent_report,
)


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


def test_revision_prompt_requires_distinct_source_reads_and_real_length():
    prompt = build_generation_prompt(
        project_name="虚构测试", section_plan=[{"key": "chapter", "title": "应急保障", "subheadings": ["队伍保障"]}],
        reference_characters=2400, revision_mode=True,
    )
    assert "三至四次有差异的知识检索" in prompt
    assert "不得少于 1872 个中文字符" in prompt
    assert "取得足以支撑本章的真实来源后立即组织最终 JSON" not in prompt


def test_unsigned_sample_draft_cannot_inherit_notice_effective_clause():
    prompt = build_generation_prompt(
        project_name="项目讨论", section_plan=[{"key": "closing", "title": "附则"}],
        document_brief={"title": "应急预案（项目讨论稿）"},
        sample_style={"notice_requires_authorized_signoff": True},
    )
    assert "不得在本稿中断言" in prompt
    content = [
        {"type": "h2", "children": [{"text": "附则"}]},
        {"type": "p", "children": [{"text": "本预案自印发之日起实施，旧通知同时废止。"}]},
    ]
    review = report_quality_review(content, section_plan=[{"title": "附则"}], bindings={},
                                   require_citations=False, draft_requires_signoff=True)
    assert not review["ok"]
    assert review["metrics"]["draft_signoff_matches"] >= 1
    assert any(item["code"] == "unsigned_draft_signoff" for item in review["issues"])
    corrected = [content[0], {"type": "p", "children": [{"text": "具体实施时间以有权机关正式印发文件为准。"}]}]
    assert report_quality_review(corrected, section_plan=[{"title": "附则"}], bindings={},
                                 require_citations=False, draft_requires_signoff=True)["ok"]


def test_single_chapter_accepts_public_bulleted_source_preface_but_not_second_json():
    plan = [{"key": "sample-section-1", "title": "总则", "subheadings": []}]
    report = {"sections": [{"section_key": "sample-section-1", "title": "总则",
                            "content_nodes": [{"type": "p", "text": "依据已上传预案整理本章内容。[1]"}],
                            "citation_refs": [1]}], "warnings": []}
    output = "Agent 已完成来源读取。\n- [1] 编制依据\n- [2] 适用范围\n\n" + json.dumps(report, ensure_ascii=False)
    assert validate_and_parse_agent_report(output, plan)[0]["title"] == "总则"
    with pytest.raises(ValueError, match="唯一且完整"):
        validate_and_parse_agent_report('[{"other": 1}]\n' + json.dumps(report, ensure_ascii=False), plan)


def test_sectional_turn_citations_are_renumbered_to_their_own_fragments():
    sections = [
        {"title": "总则", "content_nodes": [{"type": "p", "text": "依据一[1]"}], "citation_refs": [1]},
        {"title": "组织体系", "content_nodes": [{"type": "table", "items": [["单位", "依据二[1]"]]}], "citation_refs": [1]},
    ]
    citations = [
        {"message_id": "turn-a", "citation_number": 1, "chunk_id": "chunk-a"},
        {"message_id": "turn-b", "citation_number": 1, "chunk_id": "chunk-b"},
    ]
    rewritten, sources = renumber_chapter_citations(sections, ["turn-a", "turn-b"], citations)
    assert [source["chunk_id"] for source in sources] == ["chunk-a", "chunk-b"]
    assert [source["citation_number"] for source in sources] == [1, 2]
    assert rewritten[0]["content_nodes"][0]["text"] == "依据一[1]"
    assert rewritten[1]["content_nodes"][0]["items"][0][1] == "依据二[2]"
    assert rewritten[1]["citation_refs"] == [2]
    assert sections[1]["content_nodes"][0]["items"][0][1] == "依据二[1]"


def test_sectional_citation_remap_rejects_unverified_label():
    sections = [{"title": "总则", "content_nodes": [{"type": "p", "text": "未经核验[3]"}], "citation_refs": []}]
    with pytest.raises(ValueError, match="没有真实来源"):
        renumber_chapter_citations(sections, ["turn-a"], [
            {"message_id": "turn-a", "citation_number": 1, "chunk_id": "actual"},
        ])


def test_confirmed_subheading_is_not_a_business_fact_binding():
    plan = [{"key": "chapter-a", "title": "组织体系", "subheadings": ["州抗震救灾指挥部"]}]
    sections = [{"section_key": "chapter-a", "title": "组织体系", "content_nodes": [
        {"type": "p", "text": "职责依据[1]", "input_refs": ["州抗震救灾指挥部", "confirmed_staff"], "metric_refs": []},
    ]}]
    normalized, removals = normalize_agent_heading_refs(
        sections, plan, input_keys={"confirmed_staff"}, metric_keys=set(),
    )
    assert normalized[0]["content_nodes"][0]["input_refs"] == ["confirmed_staff"]
    assert normalized[0]["content_nodes"][0]["text"] == "职责依据[1]"
    assert removals == [{"section_key": "chapter-a", "heading": "州抗震救灾指挥部"}]
    assert sections[0]["content_nodes"][0]["input_refs"] == ["州抗震救灾指挥部", "confirmed_staff"]
    with pytest.raises(ValueError, match="不存在的事实输入编码"):
        normalize_agent_heading_refs(
            [{**sections[0], "content_nodes": [{"type": "p", "text": "未知", "input_refs": ["invented"]}]}],
            plan, input_keys=set(), metric_keys=set(),
        )
    with pytest.raises(ValueError, match="资料不支持的二级标题"):
        normalize_agent_heading_refs(
            [{**sections[0], "content_nodes": [{"type": "h3", "text": "海域地震事件应急"}]}],
            plan, input_keys=set(), metric_keys=set(),
        )
