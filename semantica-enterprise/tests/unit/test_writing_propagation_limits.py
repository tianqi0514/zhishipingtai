"""Real API/formula dependency chains, plus fail-closed traversal limits."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from packages.platform.controlled_writing import propagation_closure, require_complete_propagation
from packages.platform.models import WritingInputChange
from packages.platform.writing import content_hash
from tests.unit.test_writing_api import _create_project, writing_client


def _create_real_chain(client, release_id, length):
    project = _create_project(client, release_id)
    prefix = f"/api/v1/writing/projects/{project['id']}"
    facts = {}
    for key, number in (("base_amount", 2), ("increment", 1)):
        response = client.post(prefix + "/facts", json={
            "fact_key": key, "label": key, "fact_type": "official_brief",
            "value": {"number": number}, "unit": "元", "source_type": "official_brief",
            "source_id": "bounded-propagation-fixture", "verification_status": "verified",
        })
        assert response.status_code == 200, response.text
        facts[key] = response.json()
    current = facts["base_amount"]
    runs = []
    for index in range(1, length + 1):
        response = client.post(prefix + "/compute", json={
            "operation": "construction_installation_cost",
            "inputs": {"civil_cost": current["value"]["number"], "installation_cost": 1},
            "input_fact_ids": [current["id"], facts["increment"]["id"]],
            "input_fact_map": {"civil_cost": current["id"], "installation_cost": facts["increment"]["id"]},
            "output_fact_key": f"subtotal_{index}", "output_label": f"第{index}级小计", "output_unit": "元",
        })
        assert response.status_code == 200, response.text
        run = response.json()
        runs.append(run)
        current = run["generated_fact"]
    node = {"id": "final-subtotal", "type": "computed_metric", "label": "最终小计",
            "value": current["value"]["number"], "unit": "元", "computation_run_id": runs[-1]["id"],
            "children": [{"text": f"最终小计为{current['value']['number']}元。"}]}
    response = client.post("/api/v1/writing/documents", json={
        "project_id": project["id"], "title": "有界计算链测试", "content": [node],
    })
    assert response.status_code == 200, response.text
    document = response.json()
    response = client.post(f"/api/v1/writing/documents/{document['id']}/bindings", json={
        "block_id": node["id"], "block_type": node["type"], "source_type": "computation",
        "computation_run_id": runs[-1]["id"], "content_hash": content_hash(node),
        "block_content": node, "evidence_ids": runs[-1]["input_fact_ids"], "verification_status": "verified",
    })
    assert response.status_code == 200, response.text
    return project, document, runs


def _preview(client, project, document):
    return client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/preview", json={
        "document_id": document["id"],
        "changes": [{"fact_key": "base_amount", "new_value": {"number": 3}, "reason": "上游口径复核"}],
    })


def test_fourteen_real_computations_reach_final_fact_and_bound_chunk_without_truncation():
    with writing_client() as (client, _db, release):
        project, document, runs = _create_real_chain(client, release.id, 14)
        response = _preview(client, project, document)
        assert response.status_code == 200, response.text
        impact = response.json()["impact"]
        assert len(impact["calculations"]) == 14
        by_key = {row["result_key"]: row for row in impact["calculations"]}
        assert by_key["subtotal_14"]["new_value"] == 17
        closure = impact["propagation"]
        require_complete_propagation(closure)
        assert closure["truncated"] is False
        assert closure["limits"] == {"max_depth": 64, "max_nodes": 2000}
        impacted = {row["node_id"] for row in closure["impacts"]}
        assert f"computation_run:{runs[-1]['id']}" in impacted
        assert f"project_fact:{runs[-1]['generated_fact']['id']}" in impacted
        assert any(row["metadata"].get("block_id") == "final-subtotal" for row in closure["impacts"])
        assert max(row["depth"] for row in closure["impacts"]) >= 28
        assert next(row for row in impact["content_proposals"] if row["block_id"] == "final-subtotal")["selectable"] is True


def test_exceeding_depth_rejects_preview_without_persisting_partial_preview_or_mutating_facts():
    with writing_client() as (client, db, release):
        project, document, _runs = _create_real_chain(client, release.id, 33)
        facts_before = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        response = _preview(client, project, document)
        assert response.status_code == 409, response.text
        assert "安全遍历上限" in response.json()["detail"]
        assert db.scalar(select(func.count()).select_from(WritingInputChange)) == 0
        assert client.get(f"/api/v1/writing/projects/{project['id']}/facts").json() == facts_before
        assert client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version_id"] == document["current_version_id"]


def test_old_persisted_truncated_preview_cannot_apply_or_change_authority():
    with writing_client() as (client, db, release):
        project, document, _runs = _create_real_chain(client, release.id, 2)
        response = _preview(client, project, document)
        assert response.status_code == 200, response.text
        preview_id = response.json()["id"]
        row = db.get(WritingInputChange, preview_id)
        row.impact = {**row.impact, "propagation": {**row.impact["propagation"], "truncated": True}}
        db.commit()
        facts_before = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        applied = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json={
            "preview_id": preview_id, "accepted_block_ids": ["final-subtotal"],
        })
        assert applied.status_code == 409, applied.text
        assert "不能应用" in applied.json()["detail"]
        assert client.get(f"/api/v1/writing/projects/{project['id']}/facts").json() == facts_before
        assert client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version_id"] == document["current_version_id"]


def test_node_limit_allows_exact_limit_but_rejects_one_more_unvisited_target():
    one = [{"source": "a", "target": "b", "relation": "DERIVES_FROM"}]
    complete = propagation_closure(["a"], one, max_nodes=1)
    require_complete_propagation(complete)
    truncated = propagation_closure(["a"], [*one, {"source": "b", "target": "c", "relation": "DERIVES_FROM"}], max_nodes=1)
    assert len(truncated["impacts"]) == 1
    with pytest.raises(ValueError, match="不能应用"):
        require_complete_propagation(truncated)


@pytest.mark.parametrize("closure", [None, {}, {"truncated": True}])
def test_incomplete_or_legacy_unchecked_closure_is_not_an_approval(closure):
    with pytest.raises(ValueError, match="不能应用"):
        require_complete_propagation(closure)
