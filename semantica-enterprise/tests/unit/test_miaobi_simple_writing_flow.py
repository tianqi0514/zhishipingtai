from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.platform.writing_configuration import business_scenario_from_contract
from packages.platform.writing_flow import (
    assemble_report_content,
    build_generation_prompt,
    report_quality_review,
    validate_agent_edit,
    validate_and_parse_agent_report,
)


SECTIONS = [
    {"key": "general", "title": "一、总则", "instruction": "说明依据。"},
    {"key": "grading", "title": "二、灾害分级", "instruction": "说明判定。"},
    {"key": "actions", "title": "三、处置任务", "instruction": "说明任务。"},
]

ROOT = Path(__file__).resolve().parents[2]
MIAOBI_APP = (ROOT / "apps/miaobi-web/src/App.tsx").read_text(encoding="utf-8")


def test_upload_from_miaobi_switches_zhiku_to_the_task_space() -> None:
    assert "const uploadSpaceId = materials[0]?.document.space_id || knowledgeContext?.spaces[0]?.id || '';" in MIAOBI_APP
    assert "localStorage.setItem('chuanshen.pendingSpace', uploadSpaceId)" in MIAOBI_APP
    assert "localStorage.setItem('miaobi.pendingProject', project.id)" in MIAOBI_APP


def test_legacy_earthquake_inputs_are_projected_as_business_labels() -> None:
    config = business_scenario_from_contract(
        {
            "input_schema": {
                "type": "object",
                "required": ["magnitude", "rescue_available"],
                "properties": {
                    "magnitude": {"type": "number"},
                    "rescue_available": {"type": "number"},
                },
            },
            "chapter_template": {"chapters": SECTIONS},
        }
    )
    inputs = {item["key"]: item for item in config["inputs"]}
    assert inputs["magnitude"]["label"] == "震级"
    assert inputs["magnitude"]["unit"] == "级"
    assert inputs["rescue_available"]["label"] == "可用搜救人员"
    assert inputs["rescue_available"]["unit"] == "人"


def _agent_payload() -> str:
    return json.dumps(
        {
            "sections": [
                {
                    "section_key": item["key"],
                    "title": item["title"],
                    "content_nodes": [{"type": "p", "text": f"{item['title']}正文，责任单位应在两小时内完成处置并回报。[1]"}],
                    "citation_refs": [1],
                    "metric_refs": [],
                    "inference_refs": [],
                    "warnings": [],
                }
                for item in SECTIONS
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )


def test_agent_report_protocol_rejects_work_notes_and_duplicate_sections() -> None:
    parsed = validate_and_parse_agent_report(_agent_payload(), SECTIONS)
    assert [item["section_key"] for item in parsed] == ["general", "grading", "actions"]

    # The raw Session Event log may retain a model's preface for audit, but
    # only the unique final JSON envelope can cross into the formal document.
    prefaced = "Let me inspect the evidence first.\n" + _agent_payload()
    parsed_prefaced = validate_and_parse_agent_report(prefaced, SECTIONS)
    assert [item["section_key"] for item in parsed_prefaced] == ["general", "grading", "actions"]

    with pytest.raises(ValueError, match="唯一且完整"):
        validate_and_parse_agent_report(_agent_payload() + "\nextra trailing text", SECTIONS)

    duplicate = json.loads(_agent_payload())
    duplicate["sections"].append(duplicate["sections"][0])
    with pytest.raises(ValueError, match="重复生成章节"):
        validate_and_parse_agent_report(json.dumps(duplicate, ensure_ascii=False), SECTIONS)

    unsafe = json.loads(_agent_payload())
    unsafe["sections"][0]["content_nodes"][0]["text"] = "Let me inspect the search results."
    with pytest.raises(ValueError, match="工作过程"):
        validate_and_parse_agent_report(json.dumps(unsafe, ensure_ascii=False), SECTIONS)


def test_assembly_injects_authoritative_results_into_configured_sections() -> None:
    sections = validate_and_parse_agent_report(_agent_payload(), SECTIONS)
    content, bindings = assemble_report_content(
        run_id="run-1",
        title="地震应急处置报告",
        section_plan=SECTIONS,
        agent_sections=sections,
        citations=[
            {
                "citation_number": 1,
                "chunk_id": "chunk-1",
                "query_run_id": "query-1",
                "rank": 1,
                "snapshot": {
                    "title": "应急预案",
                    "document_id": "doc-1",
                    "version_id": "internal-version-id",
                    "version_number": 2,
                    "page_number": 3,
                },
            }
        ],
        computations=[
            {
                "id": "compute-1",
                "input_fact_ids": ["required", "available"],
                "result": {
                    "operation": "resource_gap",
                    "value": 180,
                    "dependencies": {"required": "rescue_required", "available": "rescue_available"},
                    "output_fact": {"fact_key": "rescue_gap", "label": "搜救人员缺口", "unit": "人"},
                },
            }
        ],
        inference_facts=[
            {
                "id": "fact-grade",
                "fact_key": "disaster_grade",
                "label": "灾害等级",
                "value": {"text": "重大地震灾害（Ⅱ级）"},
                "source_id": "reason-1",
                "source_locator": {"rule_id": "rule-1", "evidence": [{"source_fact_id": "criterion-1"}]},
                "verification_status": "verified",
            }
        ],
        selected_plan=None,
    )
    types = [item.get("type") for item in content]
    assert types.count("h2") == 3
    assert types.count("computed_metric") == 1
    assert types.count("inference_conclusion") == 1
    assert types.count("knowledge_citation") == 0  # citations are inline children
    assert len([item for item in bindings if item["block_type"] == "knowledge_citation"]) == 3
    assert {
        item["source_version"]
        for item in bindings
        if item["block_type"] == "knowledge_citation"
    } == {2}
    assert all(len(item["content_hash"]) == 64 for item in bindings)
    assert all(
        item["metadata"].get("content_hash_algorithm") == "canonical-json-v1"
        for item in bindings
    )
    report = report_quality_review(
        content,
        section_plan=SECTIONS,
        bindings={item["block_id"]: item for item in bindings},
        expected_computation_count=1,
        expected_inference_count=1,
    )
    assert report["ok"] is True
    assert report["metrics"]["duplicate_sentence_ratio"] == 0


def test_toolbox_result_uses_configured_section_and_manual_section_skips_agent() -> None:
    section_plan = [
        {"key": "summary", "title": "一、情况概述", "generation_mode": "agent"},
        {"key": "resources", "title": "二、资源测算", "generation_mode": "toolbox"},
        {"key": "notes", "title": "三、人工补充", "generation_mode": "manual"},
    ]
    payload = json.dumps(
        {
            "sections": [
                {
                    "section_key": "summary",
                    "title": "一、情况概述",
                    "content_nodes": [{"type": "p", "text": "根据已核验资料形成情况概述。"}],
                    "citation_refs": [],
                    "metric_refs": [],
                    "inference_refs": [],
                    "warnings": [],
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    agent_sections = validate_and_parse_agent_report(payload, section_plan)
    content, _ = assemble_report_content(
        run_id="configured-run",
        title="资源报告",
        section_plan=section_plan,
        agent_sections=agent_sections,
        citations=[],
        computations=[
            {
                "id": "compute-target",
                "input_fact_ids": ["required", "available"],
                "result": {
                    "operation": "resource_gap",
                    "value": 180,
                    "dependencies": {"required": "rescue_required", "available": "rescue_available"},
                    "output_fact": {"fact_key": "rescue_gap", "label": "搜救人员缺口", "unit": "人"},
                },
            }
        ],
        inference_facts=[],
        selected_plan=None,
        target_sections={"rescue_gap": ["resources"]},
    )
    resources_heading = next(index for index, node in enumerate(content) if node.get("type") == "h2" and node["children"][0]["text"] == "二、资源测算")
    notes_heading = next(index for index, node in enumerate(content) if node.get("type") == "h2" and node["children"][0]["text"] == "三、人工补充")
    assert content[resources_heading + 1]["type"] == "computed_metric"
    assert content[notes_heading + 1]["type"] == "p"
    assert content[notes_heading + 1]["children"][0]["text"] == ""


def test_quality_gate_rejects_duplicate_chapter_and_platform_process() -> None:
    content = [
        {"id": "h-1", "type": "h1", "children": [{"text": "报告"}]},
        {"id": "h-2", "type": "h2", "children": [{"text": "一、总则"}]},
        {"id": "h-3", "type": "h2", "children": [{"text": "一、总则"}]},
        {"id": "p-1", "type": "p", "children": [{"text": "本轮已调用知识图谱工具，Let me present this as the final answer。"}]},
    ]
    report = report_quality_review(content, section_plan=SECTIONS, bindings={})
    assert report["ok"] is False
    assert {item["code"] for item in report["issues"]} >= {
        "chapter_occurrence",
        "agent_work_note",
        "platform_process",
    }


def test_generation_prompt_requires_one_strict_business_result() -> None:
    prompt = build_generation_prompt(project_name="地震方案", section_plan=SECTIONS, reference_characters=10_000)
    assert "只输出一个 JSON 对象" in prompt
    assert "严禁输出思考过程" in prompt
    assert "writing_get_project_context" in prompt
    assert "knowledge_search" in prompt
    assert "不得少于 7800 个中文字符" in prompt


def test_agent_edit_rejects_work_notes_noop_and_wrong_length_direction() -> None:
    assert validate_agent_edit("rewrite", "原文", "经过调整后的正式表述。") == "经过调整后的正式表述。"
    with pytest.raises(ValueError, match="工作过程"):
        validate_agent_edit("rewrite", "原文", "Let me inspect the section first.")
    with pytest.raises(ValueError, match="相同"):
        validate_agent_edit("rewrite", "原文", "原文")
    with pytest.raises(ValueError, match="没有缩短"):
        validate_agent_edit("shorten", "需要缩短的原始文字", "这段文字实际上比原始文字更长")
    with pytest.raises(ValueError, match="没有补充"):
        validate_agent_edit("expand", "需要扩写的原始文字", "太短")
