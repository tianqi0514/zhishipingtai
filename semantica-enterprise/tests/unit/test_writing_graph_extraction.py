from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.semantica_adapter.writing_extract import (
    JointWritingExtraction,
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


def test_joint_extraction_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        JointWritingExtraction.model_validate({
            "entities": [], "claims": [], "relations": [], "metrics": [],
            "sample_profile": None, "ambiguities": [], "sql": "DROP TABLE facts",
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
                "sample_profile": {"document_type": "预案", "target_audience": "政府部门", "chapters": [], "style": {}, "table_patterns": [], "attachment_patterns": []},
                "ambiguities": [],
            },
        )


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
    assert candidate_key("a", 1, {"x": True}) == candidate_key("a", 1, {"x": True})


def test_output_budget_is_bounded_by_source_size_and_operator_ceiling() -> None:
    evidence_type = __import__(
        "packages.semantica_adapter.writing_extract", fromlist=["WritingEvidenceInput"]
    ).WritingEvidenceInput
    short = [evidence_type.model_validate(EVIDENCE[0])]
    assert writing_output_token_budget(short, 8192) == 1024

    long = [evidence_type(evidence_id="evidence-0002", text="事实" * 5000)]
    assert writing_output_token_budget(long, 8192) == 2048
    assert writing_output_token_budget(long, 1024) == 1024
