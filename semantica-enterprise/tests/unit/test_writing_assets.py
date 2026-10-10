from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from packages.platform.writing_assets_schema import (
    ASSET_KINDS, AssetCandidate, ComputationMethod, compute_method, TaskGraph,
)
from packages.platform.writing_assets import process_writing_assets, publish_assets
from packages.platform.models import WritingAssetVersion, WritingAssetRelease, WritingExtractionRun, Chunk, ContentElement, WritingEvidence
from packages.semantica_adapter.writing_assets import extract_assets
from packages.semantica_adapter.writing_extract import WritingEvidenceInput, WritingExtractionTruncated
from packages.semantica_adapter.upstream import check_writing_ontology, upgraded_capabilities
from packages.platform.writing import execute_formula
from tests.unit.test_writing_graph import graph_fixture


def variable(key="available", name="可用人员"):
    return {"key": key, "name": name, "unit": "人"}


def method():
    return {"inputs": [variable("required", "需求人数"), variable()], "output": variable("gap", "缺口"),
            "expression": "required-available", "precision": 0, "applicability": "相同资源、单位和时间口径"}


PAYLOADS = {
    "ontology": {"classes": ["组织"], "properties": []},
    "argument_chain": {"conclusion": "需要补充资源", "premises": ["供给不足"], "limitations": ["需核实库存"]},
    "inference_scenario": {"question": "哪些项目受影响", "required_inputs": ["supplier"], "rule_keys": [], "expected_output": "影响关系"},
    "variable": variable(), "task_dag": {"tasks": [{"key": "confirm", "action": "确认输入", "depends_on": []}]},
    "function": {"purpose": "缺口计算", "implementation_ref": None, "inputs": [], "output": variable("gap", "缺口")},
    "rule": {"conditions": ["项目使用产品"], "conclusion": "项目存在依赖"},
    "invariant": {"condition": "明细和总数一致", "message": "请核查总数"},
    "skill": {"purpose": "资源分析", "steps": ["确认输入", "计算缺口"], "tool_names": []},
    "report_structure": {"chapters": [{"key": "resources", "title": "资源分析", "instruction": "比较当前供需", "required_inputs": ["required", "available"]}]},
    "report_form": {"fields": [variable()]},
    "prompt": {"task": "撰写资源章节", "instructions": "引用本次数据", "origin_note": "新编译提示词，不是历史原始提示词"},
    "quality_gate": {"stage": "finalization", "checks": ["数字有依据"]},
    "reuse_policy": {"strategy": "parameterize", "conditions": ["需填写当前数据"]},
    "entity_relation_model": {"entity_types": ["组织"], "predicates": ["负责"]},
    "computation_method": method(),
}


def candidate(kind="computation_method", evidence_id="evidence-1"):
    return {"kind": kind, "key": kind, "title": ASSET_KINDS[kind], "purpose": "用于资料确认和章节写作",
            "origin": "compiled" if kind == "prompt" else "extracted_explicit",
            "evidence_ids": [evidence_id], "scope": "同类资源分析，需确认本次适用性", "payload": deepcopy(PAYLOADS[kind])}


@pytest.mark.parametrize("kind", list(ASSET_KINDS))
def test_all_sixteen_kinds_have_distinct_strict_contracts(kind):
    item = AssetCandidate.model_validate(candidate(kind))
    assert item.kind == kind
    bad = candidate(kind); bad["payload"]["fake_execution_success"] = True
    with pytest.raises(ValidationError): AssetCandidate.model_validate(bad)


@pytest.mark.parametrize("expression", ["__import__('os').system('echo x')", "required**10000", "[available][0]", "required if True else available", "sum([required,available])"])
def test_formula_injection_is_rejected(expression):
    data = method(); data["expression"] = expression
    with pytest.raises((ValueError, SyntaxError)): ComputationMethod.model_validate(data)


def test_formula_decimal_signed_gap_rounding_and_transport():
    spec = ComputationMethod.model_validate(method())
    assert compute_method(spec, {"required": 500, "available": 320}) == "180"
    assert compute_method(spec, {"required": 500, "available": 400}) == "100"
    assert compute_method(spec, {"required": 320, "available": 500}) == "-180"
    run = execute_formula("writing_asset_expression", {"required": 500, "available": 400}, parameters={"asset_method": spec.model_dump()})
    assert run["decimal_value"] == "100"
    assert run["parameters"]["asset_method"]["output"]["key"] == "gap"


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "1e100"])
def test_non_numeric_or_unbounded_input_rejected(value):
    with pytest.raises(ValueError): compute_method(ComputationMethod.model_validate(method()), {"required": 500, "available": value})


def test_unknown_missing_variable_and_zero_denominator_rejected():
    spec = method(); spec["expression"] = "required/available"
    with pytest.raises(ValueError, match="分母"): compute_method(ComputationMethod.model_validate(spec), {"required": 500, "available": 0})
    with pytest.raises(ValueError, match="输入"): compute_method(ComputationMethod.model_validate(method()), {"required": 500})
    spec["expression"] = "required-other"
    with pytest.raises(ValueError, match="变量"): ComputationMethod.model_validate(spec)


def test_task_graph_cycles_and_missing_dependencies_rejected():
    with pytest.raises(ValueError, match="循环"):
        TaskGraph.model_validate({"tasks": [{"key": "a", "action": "检索", "depends_on": ["b"]}, {"key": "b", "action": "撰写", "depends_on": ["a"]}]})
    with pytest.raises(ValueError, match="不存在"):
        TaskGraph.model_validate({"tasks": [{"key": "a", "action": "检索", "depends_on": ["missing"]}]})


def test_sample_assets_cannot_contain_historical_values():
    data = candidate("variable"); data["payload"]["value"] = 320
    with pytest.raises(ValidationError): AssetCandidate.model_validate(data)


def test_model_coverage_and_signed_evidence_contract():
    evidence = [WritingEvidenceInput(evidence_id="evidence-1", text="缺口=需求人数-可用人数")]
    valid = {"assets": [candidate()], "processed_evidence_ids": ["evidence-1"], "unresolved_items": [], "continuation_needed": False}
    result, prompt = extract_assets(evidence, material_role="reference", api_key="", model="test", base_url=None, generator=lambda _: valid)
    assert len(result.assets) == 1 and "不要求凑齐16类" in prompt
    bad = deepcopy(valid); bad["assets"][0]["evidence_ids"] = ["forged-id"]
    with pytest.raises(ValueError, match="未签发"): extract_assets(evidence, material_role="reference", api_key="", model="test", base_url=None, generator=lambda _: bad)
    bad = deepcopy(valid); bad["continuation_needed"] = True
    with pytest.raises(WritingExtractionTruncated): extract_assets(evidence, material_role="reference", api_key="", model="test", base_url=None, generator=lambda _: bad)


def test_asset_persistence_trace_retry_and_release_without_fact():
    db, tenant, user, space, doc, version = graph_fixture()
    calls = []
    def generator(prompt):
        calls.append(prompt)
        evidence = json.loads(prompt.split("Evidence：", 1)[1])
        return {"assets": [candidate("report_structure", evidence[0]["evidence_id"])],
                "processed_evidence_ids": [item["evidence_id"] for item in evidence], "unresolved_items": [], "continuation_needed": False}
    args = dict(document=doc, version=version, model_config_id=None, api_key="", model="test",
                base_url=None, material_role="sample_style", generator=generator)
    result = process_writing_assets(db, **args)
    assert result["asset_count"] == 1 and result["coverage"] == "complete"
    assert process_writing_assets(db, **args) == result
    assert len(calls) == 1
    row = db.scalar(select(WritingAssetVersion))
    with pytest.raises(ValueError, match="人工确认"): publish_assets(db, tenant.id, space.id, user.id, [row.id])
    row.status = "reviewed"; db.flush()
    release = publish_assets(db, tenant.id, space.id, user.id, [row.id])
    assert publish_assets(db, tenant.id, space.id, user.id, [row.id]).id == release.id
    snapshot = deepcopy(release.snapshot)
    row.definition = {**row.definition, "title": "不会修改历史快照"}
    assert release.snapshot == snapshot
    run = db.scalar(select(WritingExtractionRun).where(WritingExtractionRun.strategy_version == "writing-assets-v1"))
    assert run.metrics["prompt"] == calls[0] and run.metrics["coverage"] == "complete"
    db.close()


def test_native_semantica_upgrade_quality_checks_are_real():
    report = check_writing_ontology({"classes": [{"name": "Organization"}], "properties": []})
    assert report["passed"] and report["concepts"] == ["Organization"]
    malformed = check_writing_ontology({"classes": [{"name": "Organization"}], "properties": [{"name": "manages", "domain": ["Missing"], "range": ["Organization"]}]})
    assert not malformed["passed"]
    assert report["engine"] == "Semantica.OntologyQualityGate"
    assert upgraded_capabilities()["version"] == "0.7.0"


def test_parsed_only_material_extracts_from_real_elements_without_fake_chunks():
    db, tenant, user, space, doc, version = graph_fixture()
    for chunk in db.scalars(select(Chunk)):
        db.delete(chunk)
    db.flush()
    calls = []
    def generate(prompt):
        evidence = json.loads(prompt.split("Evidence：", 1)[1]); calls.append(evidence)
        return {"assets": [candidate("report_structure", evidence[0]["evidence_id"])],
            "processed_evidence_ids": [item["evidence_id"] for item in evidence], "continuation_needed": False}
    result = process_writing_assets(db, document=doc, version=version, model_config_id=None,
        api_key="", model="test", base_url=None, material_role="reference", generator=generate)
    assert result["processed_evidence_count"] > 0 and calls
    source = db.scalar(select(WritingEvidence))
    assert source.chunk_id is None and source.content_element_id is not None
    element = db.get(ContentElement, source.content_element_id)
    span = source.locator["source_span"]
    assert element.text[span["char_start"]:span["char_end"]] == source.text
    assert db.scalar(select(Chunk)) is None
    db.close()


def test_no_readable_source_is_not_reported_as_extraction_success():
    db, tenant, user, space, doc, version = graph_fixture()
    for chunk in db.scalars(select(Chunk)): db.delete(chunk)
    for element in db.scalars(select(ContentElement)): element.text = ""
    db.flush()
    with pytest.raises(ValueError, match="没有可读取"):
        process_writing_assets(db, document=doc, version=version, model_config_id=None,
            api_key="", model="test", base_url=None, material_role="reference", generator=lambda _: pytest.fail("不能调用模型"))
    db.close()


def test_invalid_batch_splits_without_accepting_partial_output_and_reuses_checkpoints():
    db, tenant, user, space, doc, version = graph_fixture()
    chunk = db.scalar(select(Chunk))
    chunk.text = "# 第一节\n\n人员需求需要核验。\n\n# 第二节\n\n供应来源需要核验。"
    db.flush()
    calls = []
    def generate(prompt):
        evidence = json.loads(prompt.split("Evidence：", 1)[1])
        calls.append(len(evidence))
        if len(evidence) > 1:
            raise WritingExtractionTruncated("output length")
        item = candidate("report_structure", evidence[0]["evidence_id"])
        item["key"] = "section_" + evidence[0]["evidence_id"].replace("-", "")[:20]
        return {"assets": [item], "processed_evidence_ids": [evidence[0]["evidence_id"]]}
    args = dict(document=doc, version=version, model_config_id=None, api_key="", model="test",
                base_url=None, material_role="sample_style", generator=generate)
    result = process_writing_assets(db, **args)
    assert result["coverage"] == "complete" and result["processed_evidence_count"] == 2
    assert calls == [2, 1, 1]
    assert process_writing_assets(db, **args) == result and calls == [2, 1, 1]
    runs = list(db.scalars(select(WritingExtractionRun)))
    assert sorted(row.status for row in runs) == ["split", "succeeded", "succeeded"]
    assert len(list(db.scalars(select(WritingAssetVersion)))) == 2
    db.close()


def test_single_source_invalid_output_still_fails_and_prompt_has_wrapper_enum():
    from packages.semantica_adapter.writing_assets import asset_prompt
    evidence = [WritingEvidenceInput(evidence_id="evidence-1", text="封面")]
    prompt = asset_prompt(evidence, "sample_style")
    assert '"extracted_explicit","inferred_from_sources","compiled"' in prompt
    assert '"additionalProperties":false' in prompt
    db, tenant, user, space, doc, version = graph_fixture()
    def bad(_): raise WritingExtractionTruncated("still truncated")
    with pytest.raises(WritingExtractionTruncated):
        process_writing_assets(db, document=doc, version=version, model_config_id=None,
            api_key="", model="test", base_url=None, material_role="reference", generator=bad)
    assert db.scalar(select(WritingAssetVersion)) is None
    assert db.scalar(select(WritingExtractionRun)).status == "failed"
    db.close()
