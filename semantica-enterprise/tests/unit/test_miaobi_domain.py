from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from apps.api.writing_schemas import WritingBlockBindingUpsert
from packages.platform.writing import (
    affected_dependency_ids,
    execute_formula,
    generate_alternative_plans,
    evaluate_earthquake_criteria,
    earthquake_reasoning_payload,
    validate_plate_content,
    validate_scenario_contract,
    validate_scenario_input,
)
from packages.platform.writing_export import _clean_citation_text, build_export_artifact
from packages.semantica_adapter.analyze import run_graph_inference


SCENARIO_ROOT = Path("demo/miaobi/scenarios")


def test_all_seven_scenario_packages_are_independent_valid_contracts() -> None:
    files = sorted(SCENARIO_ROOT.glob("*.json"))
    assert len(files) == 7
    codes = set()
    disaster_types = set()
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_scenario_contract(payload)
        codes.add(payload["code"])
        disaster_types.add(payload["disaster_type"])
        assert payload["chapter_template"]["chapters"]
        assert payload["config"]["minimum_plan_count"] == 2
        assert payload["config"]["default_plan_count"] == 3
    assert len(codes) == 7
    assert len(disaster_types) == 7


def test_non_earthquake_packages_are_explicitly_pending_business_confirmation() -> None:
    for path in SCENARIO_ROOT.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = "accepted" if payload["disaster_type"] == "earthquake" else "pending_customer_confirmation"
        assert payload["config"]["business_validation"] == expected


def test_scenario_input_validation_reports_missing_unknown_and_type_errors() -> None:
    schema = json.loads((SCENARIO_ROOT / "earthquake.v1.json").read_text(encoding="utf-8"))["input_schema"]
    findings = validate_scenario_input(schema, {"magnitude": "6.2", "unexpected": 1})
    assert {item["code"] for item in findings} == {"required", "type", "unknown"}
    assert {item["field"] for item in findings if item["code"] == "required"} == {
        "event_name", "population_density", "rescue_required", "rescue_available"
    }


@pytest.mark.parametrize(
    ("required", "available", "expected"),
    [(500, 320, 180), (500, 400, 100), (100, 120, 0)],
)
def test_resource_gap_ground_truth(required: int, available: int, expected: int) -> None:
    result = execute_formula("resource_gap", {"required": required, "available": available})
    assert result["value"] == expected


def test_formula_engine_never_evaluates_arbitrary_expression() -> None:
    with pytest.raises(ValueError, match="不支持的确定性公式"):
        execute_formula("__import__('os').system('id')", {})


def test_ground_truth_calculations() -> None:
    payload = json.loads(Path("demo/miaobi/earthquake_ground_truth.json").read_text(encoding="utf-8"))
    facts = payload["facts"]
    assert execute_formula(
        "resource_gap",
        {"required": facts["trauma_beds_required"]["value"], "available": facts["county_trauma_beds"]["value"]},
    )["value"] == 220
    assert execute_formula(
        "resource_gap",
        {"required": facts["trauma_beds_required"]["value"], "available": facts["callable_trauma_beds"]["value"]},
    )["value"] == 80
    assert execute_formula(
        "resource_gap",
        {"required": facts["tents_required"]["value"], "available": facts["tents_available"]["value"]},
    )["value"] == 1800


def test_alternative_plans_use_different_objectives_and_actual_paths() -> None:
    inputs = {
        "route_start": "指挥部",
        "route_target": "震中",
        "route_graph": {
            "指挥部": [
                {"to": "快速通道", "minutes": 10, "risk": 7, "available": True},
                {"to": "安全通道", "minutes": 24, "risk": 1, "available": True},
                {"to": "综合通道", "minutes": 17.5, "risk": 3, "available": True},
            ],
            "快速通道": [{"to": "震中", "minutes": 10, "risk": 7, "available": True}],
            "安全通道": [{"to": "震中", "minutes": 24, "risk": 1, "available": True}],
            "综合通道": [{"to": "震中", "minutes": 17.5, "risk": 3, "available": True}],
        },
        "resources": [{"name": "搜救人员", "unit": "人", "required": 500, "available": 320}],
    }
    plans = generate_alternative_plans(inputs)
    assert [item["objective"] for item in plans] == ["speed", "safety", "balanced"]
    assert plans[0]["route"]["path"] == ["指挥部", "快速通道", "震中"]
    assert plans[1]["route"]["path"] == ["指挥部", "安全通道", "震中"]
    assert plans[2]["route"]["path"] == ["指挥部", "综合通道", "震中"]
    assert len({tuple(item["route"]["path"]) for item in plans}) == 3
    assert all(item["unresolved_gaps"][0]["gap"] == 180 for item in plans)


def test_trusted_blocks_require_authoritative_reference() -> None:
    with pytest.raises(ValueError, match="缺少对应的权威来源"):
        WritingBlockBindingUpsert(
            block_id="metric-1",
            block_type="computed_metric",
            source_type="computation",
            content_hash="a" * 64,
        )

    with pytest.raises(ValueError, match="检索记录"):
        WritingBlockBindingUpsert(
            block_id="citation-1",
            block_type="knowledge_citation",
            source_type="policy_document",
            chunk_id="chunk-1",
            content_hash="a" * 64,
        )


def test_plate_content_validation_reports_stale_and_duplicate_blocks() -> None:
    content = [
        {"id": "metric-1", "type": "computed_metric", "children": [{"text": "180人"}]},
        {"id": "metric-1", "type": "paragraph", "children": [{"text": "重复标识"}]},
    ]
    issues = validate_plate_content(
        content,
        {
            "metric-1": {
                "source_type": "computation",
                "verification_status": "verified",
                "freshness_status": "stale",
            }
        },
    )
    assert {item["code"] for item in issues} == {"duplicate_block_id", "stale_binding"}


def test_dependency_impact_is_local_to_changed_fact() -> None:
    result = affected_dependency_ids(
        {"fact-rescue-available"},
        [
            {"id": "run-rescue", "input_fact_ids": ["fact-rescue-required", "fact-rescue-available"]},
            {"id": "run-tents", "input_fact_ids": ["fact-tents-required", "fact-tents-available"]},
        ],
        [
            {"block_id": "rescue-gap", "computation_run_id": "run-rescue"},
            {"block_id": "tent-gap", "computation_run_id": "run-tents"},
        ],
    )
    assert result["computation_run_ids"] == ["run-rescue"]
    assert result["block_ids"] == ["rescue-gap"]


def test_earthquake_numeric_criteria_feed_real_semantica_datalog() -> None:
    criteria = evaluate_earthquake_criteria(
        {"magnitude": {"number": 6.2}, "population_density": {"number": 305.6}}
    )
    assert all(item["value"]["boolean"] is True for item in criteria)
    facts, rules = earthquake_reasoning_payload(
        project_id="project-earthquake",
        event_name="积石山县6.2级地震",
        criteria=criteria,
    )
    result = run_graph_inference(facts=facts, rules=rules, max_results=10)
    assert result["metrics"]["derived_ground_facts"] >= 1
    assert any(
        item["predicate"] == "灾害等级"
        and item["object_value"] == "重大地震灾害（Ⅱ级）"
        for item in result["items"]
    )
    assert len(result["items"][0]["evidence"]) == 2


def test_production_export_builds_real_docx_xlsx_json_and_geojson() -> None:
    content = [
        {"id": "heading", "type": "h1", "children": [{"text": "灾情研判"}]},
        {"id": "metric", "type": "computed_metric", "children": [{"text": "搜救人员缺口：180人"}]},
    ]
    facts = [{"label": "震级", "value": {"number": 6.2}, "unit": "级", "source_type": "official_brief", "version": 1, "verification_status": "verified"}]
    computations = [{"result": {"operation": "resource_gap", "value": 180, "dependencies": {"required": "rescue_required"}, "output_fact": {"label": "搜救人员缺口", "unit": "人"}}}]
    plans = [{"name": "综合平衡", "objective": "balanced", "status": "selected", "result": {"route": {"path": ["指挥部", "震中"], "minutes": 35, "risk": 6}}}]
    audit_summary = {"knowledge_product_release": 1, "scenario_package_version": 1, "verified_fact_count": 1, "computation_count": 1, "reasoning_count": 1, "selected_plan": "综合平衡"}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for output_format in ("docx", "xlsx", "json", "geojson"):
            target = root / f"artifact.{output_format}"
            build_export_artifact(
                target,
                output_format=output_format,
                title="积石山县地震应急处置方案",
                content=content,
                facts=facts,
                computations=computations,
                plans=plans,
                audit_summary=audit_summary,
                coordinates={"指挥部": [103.2, 35.6], "震中": [103.1, 35.7]},
            )
            assert target.stat().st_size > 0
        assert zipfile.is_zipfile(root / "artifact.docx")
        assert zipfile.is_zipfile(root / "artifact.xlsx")
        assert json.loads((root / "artifact.json").read_text(encoding="utf-8"))["title"] == "积石山县地震应急处置方案"
        assert json.loads((root / "artifact.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]["type"] == "LineString"


def test_export_citation_cleans_markdown_entities_and_limits_length() -> None:
    cleaned = _clean_citation_text("地震应急预案：# 地震预案\n&gt; **响应要求** [来源](https://example.test) " + "处置" * 300)
    assert not cleaned.startswith("#")
    assert "：#" not in cleaned
    assert "&gt;" not in cleaned
    assert "**" not in cleaned
    assert "https://" not in cleaned
    assert "响应要求" in cleaned
    assert cleaned.endswith("…")
    assert len(cleaned) == 420
