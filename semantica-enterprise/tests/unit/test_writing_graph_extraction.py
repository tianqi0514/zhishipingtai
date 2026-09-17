from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.semantica_adapter.writing_extract import (
    JointWritingExtraction,
    _parse_model_json,
    candidate_key,
    extract_writing_knowledge,
    writing_extraction_prompt,
    writing_output_token_budget,
)


EVIDENCE = [{
    "evidence_id": "evidence-0001",
    "text": "截至测试时点，可用搜救人员为320人。",
    "locator": {"page": 2, "structural_path": "资源保障/人员"},
}]


def test_joint_extraction_accepts_only_signed_evidence() -> None:
    result = extract_writing_knowledge(
        EVIDENCE,
        material_role="task_data",
        api_key="unused",
        model="test",
        base_url=None,
        generator=lambda _prompt: {
            "entities": [{
                "mention_text": "搜救人员", "canonical_name": "搜救人员",
                "entity_type": "资源", "aliases": [],
                "evidence_ids": ["evidence-0001"], "confidence": 0.98,
                "needs_confirmation": False,
            }],
            "claims": [{
                "subject": "测试地区", "predicate": "可用搜救人员",
                "object_value": 320, "claim_type": "assertion", "value_type": "number",
                "unit": "人", "time_scope": {}, "applicable_scope": {"region": "测试地区"},
                "qualifiers": {}, "evidence_ids": ["evidence-0001"],
                "confidence": 0.99, "needs_confirmation": False,
            }],
            "relations": [],
            "metrics": [{
                "name": "可用搜救人员", "value": 320, "value_type": "integer", "unit": "人",
                "time_scope": {}, "applicable_scope": {"region": "测试地区"},
                "evidence_ids": ["evidence-0001"], "confidence": 0.99,
                "needs_confirmation": False,
            }],
            "sample_profile": None,
            "ambiguities": [],
        },
    )
    assert result.claims[0].object_value == 320
    assert result.metrics[0].unit == "人"


def test_joint_extraction_retries_one_malformed_structured_response() -> None:
    prompts: list[str] = []

    def generator(prompt: str) -> dict:
        prompts.append(prompt)
        if len(prompts) == 1:
            raise RuntimeError(
                "Failed to parse JSON from OpenAI response: "
                "Expecting ',' delimiter"
            )
        return {
            "entities": [], "claims": [], "relations": [], "metrics": [],
            "sample_profile": None, "ambiguities": [],
        }

    result = extract_writing_knowledge(
        EVIDENCE,
        material_role="task_data",
        api_key="unused",
        model="test",
        base_url=None,
        max_retries=1,
        generator=generator,
    )

    assert result == JointWritingExtraction()
    assert len(prompts) == 2
    assert "纠错重试" in prompts[1]


def test_joint_extraction_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        JointWritingExtraction.model_validate({
            "entities": [], "claims": [], "relations": [], "metrics": [],
            "sample_profile": None, "ambiguities": [], "sql": "DROP TABLE facts",
        })


def test_joint_extraction_preserves_structured_ambiguities_as_advisory_text() -> None:
    result = JointWritingExtraction.model_validate({
        "entities": [], "claims": [], "relations": [], "metrics": [],
        "sample_profile": None,
        "ambiguities": [{
            "field": "metrics[0].unit",
            "reason": "原表使用合并表头，单位需人工确认",
            "evidence_ids": ["evidence-0001"],
        }],
    })
    assert result.ambiguities == [
        '{"evidence_ids": ["evidence-0001"], "field": "metrics[0].unit", '
        '"reason": "原表使用合并表头，单位需人工确认"}'
    ]


def test_scope_text_is_deterministically_normalized_but_other_types_are_rejected() -> None:
    result = JointWritingExtraction.model_validate({
        "entities": [], "claims": [], "relations": [],
        "metrics": [{
            "name": "县域可用床位", "value": 110, "unit": "张",
            "applicable_scope": "测试地区县域范围",
            "evidence_ids": ["evidence-0001"], "confidence": 0.98,
        }],
        "sample_profile": None, "ambiguities": [],
    })
    assert result.metrics[0].applicable_scope == {"description": "测试地区县域范围"}
    with pytest.raises(ValidationError):
        JointWritingExtraction.model_validate({
            "entities": [], "claims": [], "relations": [],
            "metrics": [{
                "name": "县域可用床位", "value": 110,
                "applicable_scope": ["测试地区"],
                "evidence_ids": ["evidence-0001"], "confidence": 0.98,
            }],
            "sample_profile": None, "ambiguities": [],
        })


def test_joint_extraction_rejects_forged_evidence_id() -> None:
    with pytest.raises(ValueError, match="未签发"):
        extract_writing_knowledge(
            EVIDENCE,
            material_role="task_data",
            api_key="unused",
            model="test",
            base_url=None,
            generator=lambda _prompt: {
                "entities": [],
                "claims": [{
                    "subject": "测试地区", "predicate": "震级", "object_value": 6.2,
                    "claim_type": "assertion", "value_type": "number", "unit": None,
                    "time_scope": {}, "applicable_scope": {}, "qualifiers": {},
                    "evidence_ids": ["forged-evidence"], "confidence": 0.9,
                    "needs_confirmation": False,
                }],
                "relations": [], "metrics": [], "sample_profile": None, "ambiguities": [],
            },
        )


def test_sample_profile_cannot_emit_business_facts() -> None:
    with pytest.raises(ValueError, match="样稿抽取不得"):
        extract_writing_knowledge(
            EVIDENCE,
            material_role="sample_style",
            api_key="unused",
            model="test",
            base_url=None,
            generator=lambda _prompt: {
                "entities": [],
                "claims": [{
                    "subject": "样稿地区", "predicate": "震级", "object_value": 6.2,
                    "claim_type": "assertion", "value_type": "number", "unit": None,
                    "time_scope": {}, "applicable_scope": {}, "qualifiers": {},
                    "evidence_ids": ["evidence-0001"], "confidence": 0.9,
                    "needs_confirmation": False,
                }],
                "relations": [], "metrics": [],
                "sample_profile": {"document_type": "预案", "target_audience": "政府部门", "chapters": [], "style": {}, "table_patterns": [], "attachment_patterns": [], "evidence_ids": ["evidence-0001"]},
                "ambiguities": [],
            },
        )


def test_sample_profile_normalizes_style_shorthand_and_keeps_signed_evidence() -> None:
    result = extract_writing_knowledge(
        EVIDENCE,
        material_role="sample_style",
        api_key="unused",
        model="test",
        base_url=None,
        generator=lambda _prompt: {
            "entities": [], "claims": [], "relations": [], "metrics": [],
            "sample_profile": {
                "document_type": "应急方案", "target_audience": "应急管理部门",
                "chapters": [{
                    "title": "事件基本情况", "responsibility": "说明灾情和范围",
                    "level": 1, "citation_required": True,
                }],
                "style": "正式、审慎", "table_patterns": ["任务清单"],
                "attachment_patterns": ["资源表"], "evidence_ids": ["evidence-0001"],
            },
            "ambiguities": [],
        },
    )
    assert result.sample_profile is not None
    assert result.sample_profile.style == {"description": "正式、审慎"}
    assert result.sample_profile.evidence_ids == ["evidence-0001"]


def test_sample_prompt_keeps_signed_text_but_omits_repeated_locator_metadata() -> None:
    evidence_type = __import__(
        "packages.semantica_adapter.writing_extract", fromlist=["WritingEvidenceInput"]
    ).WritingEvidenceInput
    prompt = writing_extraction_prompt(
        [evidence_type.model_validate(EVIDENCE[0])], material_role="sample_style",
    )
    assert '"evidence_id": "evidence-0001"' in prompt
    assert "截至测试时点" in prompt
    assert '"locator"' not in prompt


def test_prompt_describes_single_joint_request_and_candidate_keys_are_stable() -> None:
    prompt = writing_extraction_prompt(
        [
            # Validation occurs before prompt construction in the public path.
            # The direct constructor keeps this test focused on the contract.
            __import__(
                "packages.semantica_adapter.writing_extract", fromlist=["WritingEvidenceInput"]
            ).WritingEvidenceInput.model_validate(EVIDENCE[0])
        ],
        material_role="task_data",
    )
    assert "一次联合识别" in prompt
    assert "evidence-0001" in prompt
    assert '"mention_text"' in prompt
    assert '"canonical_name"' in prompt
    assert "字段名必须逐字一致" in prompt
    assert "四个数组合计最多12项" in prompt
    assert "ambiguities 最多3项" in prompt
    assert candidate_key("a", 1, {"x": True}) == candidate_key("a", 1, {"x": True})


def test_output_budget_is_bounded_by_source_size_and_operator_ceiling() -> None:
    evidence_type = __import__(
        "packages.semantica_adapter.writing_extract", fromlist=["WritingEvidenceInput"]
    ).WritingEvidenceInput
    short = [evidence_type.model_validate(EVIDENCE[0])]
    assert 1024 <= writing_output_token_budget(short, 8192) < 1280

    long = [evidence_type(evidence_id="evidence-0002", text="事实" * 5000)]
    assert writing_output_token_budget(long, 8192) == 2048
    assert writing_output_token_budget(long, 1024) == 1024


def test_joint_extraction_deterministically_bounds_candidate_expansion() -> None:
    candidate = {
        "mention_text": "对象", "canonical_name": "对象", "entity_type": "其他",
        "evidence_ids": ["evidence-0001"], "confidence": 0.9,
    }
    result = JointWritingExtraction.model_validate({
        "entities": [candidate for _ in range(13)],
        "claims": [], "relations": [], "metrics": [],
        "sample_profile": None, "ambiguities": [],
    })
    assert len(result.entities) == 4
    assert "原始 Evidence 人工补充" in result.ambiguities[0]


def test_model_json_repairs_missing_comma_but_keeps_exact_contract() -> None:
    class Provider:
        @staticmethod
        def _parse_json(value: str) -> dict:
            import json

            return json.loads(value)

    malformed = (
        '{"entities":[] "claims":[],"relations":[],"metrics":[],'
        '"sample_profile":null,"ambiguities":[]}'
    )
    assert _parse_model_json(Provider(), malformed, "stop") == {
        "entities": [], "claims": [], "relations": [], "metrics": [],
        "sample_profile": None, "ambiguities": [],
    }


def test_model_json_never_repairs_truncated_or_incomplete_contract() -> None:
    class Provider:
        @staticmethod
        def _parse_json(value: str) -> dict:
            import json

            return json.loads(value)

    with pytest.raises(ValueError, match="截断"):
        _parse_model_json(Provider(), '{"entities": [', "length")
    with pytest.raises(ValueError, match="缺少顶层键"):
        _parse_model_json(Provider(), '{"entities": []}', "stop")
