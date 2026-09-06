from __future__ import annotations

from pathlib import Path

import pytest

from scripts.demo.prepare_guolian_governance import (
    GovernanceDemoPreparer,
    GovernanceReport,
    ScenarioResult,
    SCENARIOS,
    SPACE_CODE,
    SPACE_NAME,
    build_dry_run,
    marker,
    profile_changes,
    select_named_entity,
)
from scripts.demo.guolian_demo import DemoError


ROOT = Path(__file__).resolve().parents[2]


def test_governance_matrix_covers_requested_business_scenarios() -> None:
    assert {row.key for row in SCENARIOS} == {
        "policy_versions",
        "duplicate_documents",
        "organization_alias",
        "supplier_name_conflict",
        "classification_correction",
        "ocr_low_confidence",
        "missing_metadata",
        "missing_relation",
        "expired_knowledge",
        "sensitive_data",
        "rollback",
    }
    assert next(row for row in SCENARIOS if row.key == "duplicate_documents").support == "supported"
    assert next(row for row in SCENARIOS if row.key == "expired_knowledge").support == "partial"
    assert all(row.real_boundary.strip() for row in SCENARIOS)


def test_dry_run_is_explicitly_non_destructive_and_honest() -> None:
    plan = build_dry_run()
    assert plan["dataset"] == SPACE_CODE
    assert plan["space_name"] == SPACE_NAME
    assert plan["destructive"] is False
    assert plan["operations_are_rolled_back"] is True
    assert not any(row["support"] == "gap" for row in plan["scenarios"])


def test_marker_is_stable_and_does_not_contain_credentials() -> None:
    assert marker("organization_alias") == "[guolian-governance-demo:organization_alias]"
    assert "password" not in marker("organization_alias").lower()
    assert "token" not in marker("organization_alias").lower()


def test_entity_selection_prefers_demo_owned_canonical_entity() -> None:
    rows = [
        {
            "id": "automatic",
            "canonical_name": "数字科技公司",
            "entity_type": "组织",
            "source_count": 7,
            "confidence": 0.9,
            "properties": {},
        },
        {
            "id": "demo",
            "canonical_name": "数字科技公司",
            "entity_type": "组织",
            "source_count": 0,
            "confidence": 1,
            "properties": {"dataset": SPACE_CODE},
        },
        {
            "id": "wrong-type",
            "canonical_name": "数字科技公司",
            "entity_type": "其他",
            "source_count": 99,
            "confidence": 1,
            "properties": {"dataset": SPACE_CODE},
        },
    ]
    assert select_named_entity(rows, "数字科技公司", entity_type="组织")["id"] == "demo"
    assert select_named_entity(rows, "不存在", entity_type="组织") is None


def test_entity_selection_uses_platform_nfkc_casefold_identity() -> None:
    rows = [{
        "id": "nexus",
        "canonical_name": "Nexusone",
        "normalized_name": "nexusone",
        "entity_type": "产品",
        "properties": {"dataset": SPACE_CODE},
    }]
    assert select_named_entity(rows, "NexusOne", entity_type="产品")["id"] == "nexus"


def test_profile_changes_compares_effective_projection_not_automatic_value() -> None:
    profile = {
        "automatic": {"classification": "项目材料", "tags": []},
        "effective": {"classification": "项目周报", "tags": ["人工复核"]},
    }
    assert profile_changes(
        profile,
        {"classification": "项目周报", "tags": ["人工复核"], "time_range": {"start": "2026-01-01"}},
    ) == {"time_range": {"start": "2026-01-01"}}


def test_report_preserves_real_status_counts_and_limitations() -> None:
    report = GovernanceReport(
        space_id="space-demo",
        space_name=SPACE_NAME,
        scenarios=[
            ScenarioResult("one", "真实闭环", "supported", "passed", ["真实接口"], []),
            ScenarioResult("two", "能力缺口", "gap", "gap", [], ["没有后端闭环"]),
        ],
    ).as_dict()
    assert report["summary"] == {"passed": 1, "gap": 1}
    assert report["scenarios"][1]["limitations"] == ["没有后端闭环"]


class _SpaceOnlyApi:
    def __init__(self, spaces):
        self.spaces = spaces

    def call(self, method: str, path: str, **_kwargs):
        assert method == "GET"
        assert path == "/spaces"
        return self.spaces


@pytest.mark.parametrize(
    "spaces",
    [
        [],
        [{"id": "wrong", "code": SPACE_CODE, "name": "同编码的其他空间"}],
        [
            {"id": "one", "code": SPACE_CODE, "name": SPACE_NAME},
            {"id": "two", "code": SPACE_CODE, "name": SPACE_NAME},
        ],
    ],
)
def test_preparer_refuses_missing_ambiguous_or_mismatched_space(spaces) -> None:
    with pytest.raises(DemoError):
        GovernanceDemoPreparer(_SpaceOnlyApi(spaces))._load_space()


def test_script_has_no_delete_call_or_direct_storage_access() -> None:
    source = (ROOT / "scripts" / "demo" / "prepare_guolian_governance.py").read_text()
    assert 'self.api.call("DELETE"' not in source
    assert "sqlalchemy" not in source
    assert "boto" not in source
    assert "QdrantClient" not in source
    assert "FalkorDB(" not in source
    assert "OpenSearch(" not in source
