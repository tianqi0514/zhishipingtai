"""REST and database contracts; fixture extraction is not a live-model test."""
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select
import pytest

from apps.api.writing_assets import router
from packages.platform.models import (
    Document, DocumentVersion, WritingEvidence, WritingExtractionRun, WritingAssetVersion,
    WritingProject, KnowledgeSpace, User, ProjectFact, WritingDocumentVersion,
    ComputationRun, ComputationDefinitionVersion, WritingDocument,
)
from packages.platform.writing_graph import content_hash
from packages.platform.writing_assets import validate_asset
from packages.platform.writing_assets_schema import AssetCandidate
from tests.unit.test_writing_api import writing_client, _create_project
from tests.unit.test_writing_assets import candidate


def seed_assets(db, project_id):
    project = db.get(WritingProject, project_id)
    space = db.scalar(select(KnowledgeSpace))
    project.knowledge_space_id = space.id
    user = db.scalar(select(User))
    doc = Document(tenant_id=user.tenant_id, space_id=space.id, title="历史保障方案", owner_id=user.id, status="ready")
    db.add(doc); db.flush()
    version = DocumentVersion(tenant_id=user.tenant_id, document_id=doc.id, version_number=1,
        filename="历史保障方案.md", content_type="text/markdown", size=100, sha256="f"*64,
        object_key="fixtures/history.md", status="ready")
    db.add(version); db.flush()
    evidence = WritingEvidence(tenant_id=user.tenant_id, space_id=space.id, document_id=doc.id,
        document_version_id=version.id, evidence_key="history", filename=version.filename,
        file_version=1, text="资源缺口按需求人数减可用人数计算；历史可用人数320人。", content_hash="d"*64,
        locator={"page": 1})
    db.add(evidence); db.flush()
    run = WritingExtractionRun(tenant_id=user.tenant_id, space_id=space.id, document_version_id=version.id,
        strategy_version="writing-assets-v1", prompt_schema_version="writing-assets-v1", batch_key="f"*64,
        evidence_ids=[evidence.id], status="succeeded", metrics={"prompt": "fixture protocol input", "output": {}, "coverage": "complete"})
    db.add(run); db.flush()
    result = []
    for kind in ("report_structure", "computation_method", "skill"):
        item = AssetCandidate.model_validate(candidate(kind, evidence.id))
        row = WritingAssetVersion(tenant_id=user.tenant_id, space_id=space.id, extraction_run_id=run.id,
            asset_key=kind, kind=kind, version=1, title=item.title, definition=item.model_dump(),
            checksum=content_hash(item.model_dump()), validation=validate_asset(item), status="candidate")
        db.add(row); db.flush(); result.append(row)
    db.commit()
    return space, result


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def review(client, row, **extra):
    return client.post(f"/api/v1/writing-assets/{row.id}/review", json={
        "checksum": row.checksum, "action": "accept", "reason": "已核对原文与当前适用性", **extra})


@pytest.mark.parametrize("metric_text", ["资源缺口180人。", ""])
def test_asset_review_publish_adopt_compute_and_exact_bound_impact(metric_text):
    with writing_client() as (client, db, release):
        client.app.include_router(router, prefix="/api/v1")
        project = _create_project(client, release.id)
        space, (structure, formula, skill) = seed_assets(db, project["id"])
        assert len(checked(client.get("/api/v1/writing-assets/kinds"))["kinds"]) == 16
        checked(client.post("/api/v1/writing-assets/releases", json={"space_id": space.id, "asset_ids": [formula.id]}), 422)
        checked(review(client, formula), 422)
        checked(review(client, formula, unit_confirmed=True, tests=[
            {"inputs": {"required": 500, "available": 320}, "expected": "180"},
            {"inputs": {"required": "500.0", "available": "320.0"}, "expected": "180"}]), 422)
        checked(review(client, formula, unit_confirmed=True, tests=[
            {"inputs": {"required": 500, "available": 320}, "expected": "180"},
            {"inputs": {"required": 500, "available": 500}, "expected": "0"}]))
        checked(review(client, structure)); checked(review(client, skill))
        assert skill.validation["execution"] == "not_implemented"
        asset_release = checked(client.post("/api/v1/writing-assets/releases", json={"space_id": space.id, "asset_ids": [structure.id, formula.id]}))
        article = checked(client.post("/api/v1/writing/documents", json={"project_id": project["id"], "title": "新事件保障方案"}))
        payload = {"release_id": asset_release["id"], "asset_ids": [structure.id, formula.id],
            "base_version_id": article["current_version_id"], "scope_confirmed": True, "adopt_outline": True}
        adopted = checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/adopt", json=payload))
        assert adopted["document"]["applicability"]["native_outline"]["sections"][0]["title"] == "资源分析"
        assert set(adopted["required_current_inputs"]) == {"required", "available"}
        # Definitions do not transport the historical number as an adopted Fact.
        initial = checked(client.get(f"/api/v1/writing/projects/{project['id']}/facts"))
        assert all(row["value"] == {} and row["verification_status"] == "unverified" for row in initial)
        checked(client.post(f"/api/v1/writing/projects/{project['id']}/facts/{initial[0]['id']}/confirm", json={
            "decision": "confirm", "reason": "不能确认空输入"}), 422)
        assert initial[0]["source_locator"]["value_type"] == "number"
        checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/compute/{formula.id}"), 409)
        for key, number in (("required", 500), ("available", 320)):
            checked(client.post(f"/api/v1/writing/projects/{project['id']}/facts", json={
                "fact_key": key, "label": key, "fact_type": "manual_input", "value": {"number": number},
                "unit": "人", "source_type": "manual_input", "verification_status": "verified"}))
        computed = checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/compute/{formula.id}"))
        assert computed["fact"]["value"]["number"] == 180
        repeated = checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/compute/{formula.id}"))
        assert repeated["reused"] and repeated["run"]["id"] == computed["run"]["id"]
        assert repeated["fact"]["id"] == computed["fact"]["id"]
        run, fact = computed["run"], computed["fact"]
        from apps.api.writing import _article_computation_rows
        article_row = db.get(WritingDocument, article["id"])
        user = db.scalar(select(User))
        project_row = db.get(WritingProject, project["id"])
        # Adopted formula results remain usable when the legacy scenario's
        # disaster calculations are disabled. Unadopted versions never leak.
        original_definition = db.get(ComputationDefinitionVersion, run["definition_version_id"])
        other_definition = ComputationDefinitionVersion(
            tenant_id=user.tenant_id, definition_id=original_definition.definition_id, version=2,
            expression="required-available", operation="writing_asset_expression",
            checksum="unadopted", input_schema={}, output_schema={}, created_by=user.id,
        )
        db.add(other_definition); db.flush()
        other_run = ComputationRun(tenant_id=user.tenant_id, project_id=project_row.id,
            definition_version_id=other_definition.id, status="succeeded",
            created_by=user.id, checksum="unadopted-run",
            inputs={"required": 500, "available": 320},
            result={"output_fact": {"fact_key": "gap", "value": {"number": 999}},
                    "dependencies": {"required": "required", "available": "available"}})
        db.add(other_run)
        db.commit()
        selected_runs = _article_computation_rows(db, project_row, article_row, user, legacy_enabled=False)
        assert [item.id for item in selected_runs] == [run["id"]]
        other_run.status = "failed"; db.commit()
        metric = {"id": "controlled-gap", "type": "computed_metric", "label": "资源缺口", "value": 180,
            "unit": "人", "computation_run_id": run["id"], "children": [{"text": metric_text}]}
        plain = {"id": "unbound", "type": "p", "children": [{"text": "无关台账320笔，原值180。"}]}
        version = checked(client.post(f"/api/v1/writing/documents/{article['id']}/versions", json={"content": [metric, plain], "change_summary": "绑定结果"}))
        checked(client.post(f"/api/v1/writing/documents/{article['id']}/bindings", json={
            "block_id": metric["id"], "block_type": "computed_metric", "source_type": "computation",
            "computation_run_id": run["id"], "fact_id": fact["id"], "content_hash": content_hash(metric),
            "block_content": metric, "verification_status": "verified"}))
        preview = checked(client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/preview", json={
            "document_id": article["id"], "changes": [{"fact_key": "available", "new_value": {"number": 400}, "reason": "本次资源增加"}]}))
        assert preview["impact"]["calculations"][0]["new_value"] == 100
        proposal = preview["impact"]["content_proposals"][0]
        assert proposal["old_text"] == (metric_text or "缺口：180人")
        assert "缺口" in proposal["section"]
        assert db.get(WritingDocumentVersion, version["id"]).content[0]["value"] == 180
        applied = checked(client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json={"preview_id": preview["id"]}))
        assert applied["document_version"]["content"][0]["value"] == 100
        assert applied["document_version"]["content"][1] == plain
        assert db.get(WritingDocumentVersion, version["id"]).content[0]["value"] == 180
        # The method and original evidence remain immutable in the adopted release.
        assert asset_release["snapshot"]["assets"]
        checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/adopt", json=payload), 409)


def test_report_generation_executes_adopted_methods_with_legacy_toolbox_disabled():
    with writing_client() as (client, db, release):
        client.app.include_router(router, prefix="/api/v1")
        project = _create_project(client, release.id)
        space, (structure, formula, _) = seed_assets(db, project["id"])
        checked(review(client, structure))
        checked(review(client, formula, unit_confirmed=True, tests=[
            {"inputs": {"required": 500, "available": 320}, "expected": "180"},
            {"inputs": {"required": 500, "available": 500}, "expected": "0"}]))
        published = checked(client.post("/api/v1/writing-assets/releases", json={
            "space_id": space.id, "asset_ids": [structure.id, formula.id]}))
        article = checked(client.post("/api/v1/writing/documents", json={
            "project_id": project["id"], "title": "按采用方法写作", "document_type": "custom"}))
        checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/adopt", json={
            "release_id": published["id"], "asset_ids": [structure.id, formula.id],
            "base_version_id": article["current_version_id"], "scope_confirmed": True, "adopt_outline": True}))
        for key, value in (("required", 500), ("available", 320)):
            checked(client.post(f"/api/v1/writing/projects/{project['id']}/facts", json={
                "fact_key": key, "label": key, "fact_type": "manual_input", "value": {"number": value},
                "unit": "人", "source_type": "manual_input", "verification_status": "verified"}))
        generation = checked(client.post(f"/api/v1/writing/projects/{project['id']}/generate-report",
            json={"document_id": article["id"], "allow_partial": False}))
        items = generation["toolbox_result"]["computations"]["items"]
        assert len(items) == 1 and items[0]["fact"]["value"]["number"] == 180
        assert generation["toolbox_result"]["reasoning"]["skipped"]
        assert generation["status"] == "awaiting_agent"


def test_asset_revision_permissions_dependency_cycles_and_source_deletion():
    with writing_client() as (client, db, release):
        client.app.include_router(router, prefix="/api/v1")
        project = _create_project(client, release.id)
        space, (structure, formula, _) = seed_assets(db, project["id"])
        wrong = deepcopy(structure.definition); wrong["evidence_ids"] = ["forged"]
        checked(client.post(f"/api/v1/writing-assets/{structure.id}/revise", json={"checksum": structure.checksum, "definition": wrong}), 422)
        other = User(tenant_id="different-tenant", username="other", display_name="其他租户", password_hash="unused", enabled=True, is_admin=True)
        db.add(other); db.commit()
        from packages.platform.security import create_access_token
        denied = client.get(f"/api/v1/writing-assets/{structure.id}/trace", headers={"Authorization": f"Bearer {create_access_token(other.id, other.tenant_id, True)}"})
        checked(denied, 404)
        checked(review(client, structure))
        evidence = db.get(WritingEvidence, structure.definition["evidence_ids"][0])
        evidence.deleted_at = datetime.now(timezone.utc); db.commit()
        checked(client.post("/api/v1/writing-assets/releases", json={"space_id": space.id, "asset_ids": [structure.id]}), 422)


def test_empty_space_can_prepare_materials_without_faking_a_release():
    with writing_client() as (client, db, release):
        user = db.scalar(select(User))
        space = KnowledgeSpace(tenant_id=user.tenant_id, code="empty-preparation", name="空白资料准备空间", owner_id=user.id)
        db.add(space); db.commit()
        project = checked(client.post("/api/v1/writing/projects", json={"name": "先上传再写作", "space_id": space.id}))
        assert project["knowledge_space_id"] == space.id
        assert project["knowledge_product_release_id"] is None
        article = checked(client.post("/api/v1/writing/documents", json={"project_id": project["id"], "title": "空白起草"}))
        assert article["status"] == "draft"
        attached = checked(client.post(f"/api/v1/writing/projects/{project['id']}/knowledge-space", json={"space_id": space.id}))
        assert attached["source_preparation_only"] and attached["knowledge_product_release_id"] is None


def test_adopt_maps_business_input_names_without_historical_values_or_computed_placeholders():
    with writing_client() as (client, db, release):
        client.app.include_router(router, prefix="/api/v1")
        project = _create_project(client, release.id)
        space, (structure, formula, _) = seed_assets(db, project["id"])
        definition = deepcopy(structure.definition)
        definition["payload"]["chapters"][0]["required_inputs"] = ["需求人数", "gap", "事件说明"]
        formula_definition = deepcopy(formula.definition)
        formula_definition["used_in_sections"] = ["一、资源分析"]
        formula.definition = formula_definition
        formula.checksum = content_hash(formula_definition)
        structure.definition = definition
        structure.checksum = content_hash(definition)
        structure.status = formula.status = "reviewed"
        db.commit()
        published = checked(client.post("/api/v1/writing-assets/releases", json={"space_id": space.id, "asset_ids": [structure.id, formula.id]}))
        article = checked(client.post("/api/v1/writing/documents", json={"project_id": project["id"], "title": "新方案"}))
        adopted = checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/adopt", json={
            "release_id": published["id"], "asset_ids": [structure.id, formula.id],
            "base_version_id": article["current_version_id"], "scope_confirmed": True, "adopt_outline": True}))
        required = adopted["document"]["applicability"]["native_outline"]["sections"][0]["required_inputs"]
        assert adopted["document"]["applicability"]["native_outline"]["sections"][0]["toolbox_outputs"] == ["gap"]
        assert required[:2] == ["required", "gap"]
        facts = checked(client.get(f"/api/v1/writing/projects/{project['id']}/facts"))
        assert not any(fact["fact_key"] == "gap" for fact in facts)
        assert any(fact["fact_key"] == required[2] and fact["label"] == "事件说明" for fact in facts)
        assert all(fact["value"] == {} and fact["verification_status"] == "unverified" for fact in facts)


def test_confirmation_accepts_numeric_zero_but_rejects_whitespace():
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        for value, expected in (({"number": 0}, 200), ({"text": "   "}, 422)):
            fact = checked(client.post(f"/api/v1/writing/projects/{project['id']}/facts", json={
                "fact_key": "optional", "label": "可选输入", "fact_type": "manual_input", "value": value,
                "source_type": "manual_input", "verification_status": "unverified"}))
            checked(client.post(f"/api/v1/writing/projects/{project['id']}/facts/{fact['id']}/confirm", json={
                "decision": "confirm", "reason": "已核对实际输入"}), expected)


def test_published_formula_bundle_rejects_conflicting_units_outputs_and_undeclared_producer():
    with writing_client() as (client, db, release):
        client.app.include_router(router, prefix="/api/v1")
        project = _create_project(client, release.id)
        space, (_, formula, _) = seed_assets(db, project["id"])
        checked(review(client, formula, unit_confirmed=True, tests=[
            {"inputs": {"required": 500, "available": 320}, "expected": "180"},
            {"inputs": {"required": 500, "available": 500}, "expected": "0"}]))
        other = deepcopy(formula.definition)
        other["key"] = "other_formula"
        row = WritingAssetVersion(tenant_id=formula.tenant_id, space_id=space.id,
            extraction_run_id=formula.extraction_run_id, asset_key="other", kind="computation_method",
            version=1, title="另一种口径", definition=other, checksum=content_hash(other),
            validation=deepcopy(formula.validation), status="reviewed")
        db.add(row); db.commit()
        payload = {"space_id": space.id, "asset_ids": [formula.id, row.id]}
        assert "多个计算方法" in checked(client.post("/api/v1/writing-assets/releases", json=payload), 422)["detail"]
        other["payload"]["output"]["key"] = "other_gap"
        other["payload"]["inputs"][0]["unit"] = "张"
        row.definition = deepcopy(other); db.commit()
        assert "单位" in checked(client.post("/api/v1/writing-assets/releases", json=payload), 422)["detail"]
        other["payload"]["inputs"] = [{"key": "gap", "name": "缺口", "unit": "人", "value_type": "number"}]
        other["payload"]["expression"] = "gap*2"
        row.definition = deepcopy(other); db.commit()
        assert "前置方法" in checked(client.post("/api/v1/writing-assets/releases", json=payload), 422)["detail"]
        other["dependencies"] = [formula.definition["key"]]
        row.definition = deepcopy(other); db.commit()
        checked(client.post("/api/v1/writing-assets/releases", json=payload))


@pytest.mark.parametrize("zero_denominator", [False, True])
def test_formula_chain_dependency_order_snapshot_guard_and_atomic_failure(zero_denominator):
    with writing_client() as (client, db, release):
        client.app.include_router(router, prefix="/api/v1")
        project = _create_project(client, release.id)
        space, (_, formula, _) = seed_assets(db, project["id"])
        checked(review(client, formula, unit_confirmed=True, tests=[
            {"inputs": {"required": 500, "available": 320}, "expected": "180"},
            {"inputs": {"required": 500, "available": 500}, "expected": "0"}]))
        definition = deepcopy(formula.definition)
        definition.update(key="dependent_ratio", title="缺口比例", dependencies=[formula.definition["key"]])
        definition["payload"].update(expression="gap/available", precision=2,
            inputs=[{"key": "gap", "name": "缺口", "unit": "人", "value_type": "number"},
                    {"key": "available", "name": "可用人员", "unit": "人", "value_type": "number"}],
            output={"key": "ratio", "name": "缺口比例", "unit": "", "value_type": "number"})
        dependent = WritingAssetVersion(tenant_id=formula.tenant_id, space_id=space.id,
            extraction_run_id=formula.extraction_run_id, asset_key="dependent", kind="computation_method",
            version=1, title="缺口比例", definition=definition, checksum=content_hash(definition),
            validation=validate_asset(AssetCandidate.model_validate(definition)), status="candidate")
        db.add(dependent); db.commit()
        checked(review(client, dependent, unit_confirmed=True, tests=[
            {"inputs": {"gap": 180, "available": 320}, "expected": "0.56"},
            {"inputs": {"gap": 0, "available": 500}, "expected": "0"}]))
        published = checked(client.post("/api/v1/writing-assets/releases", json={
            "space_id": space.id, "asset_ids": [dependent.id, formula.id]}))
        article = checked(client.post("/api/v1/writing/documents", json={
            "project_id": project["id"], "title": "计算链核验"}))
        adopted = {"release_id": published["id"], "asset_ids": [dependent.id, formula.id],
                   "base_version_id": article["current_version_id"], "scope_confirmed": True}
        missing = {**adopted, "asset_ids": [dependent.id]}
        checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/adopt", json=missing), 422)
        checked(client.post(f"/api/v1/writing-assets/documents/{article['id']}/adopt", json=adopted))
        for key, number in (("required", 500), ("available", 0 if zero_denominator else 320)):
            checked(client.post(f"/api/v1/writing/projects/{project['id']}/facts", json={
                "fact_key": key, "label": key, "fact_type": "manual_input", "value": {"number": number},
                "unit": "人", "source_type": "manual_input", "verification_status": "verified"}))
        target = f"/api/v1/writing-assets/documents/{article['id']}/compute"
        payload = {"base_version_id": article["current_version_id"], "release_id": published["id"], "checksum": published["checksum"]}
        checked(client.post(target, json={**payload, "checksum": "stale"}), 409)
        response = client.post(target, json=payload)
        if zero_denominator:
            checked(response, 422)
            assert not list(db.scalars(select(ComputationRun).where(ComputationRun.project_id == project["id"])))
            assert not list(db.scalars(select(ProjectFact).where(ProjectFact.project_id == project["id"], ProjectFact.fact_key == "gap")))
        else:
            result = checked(response)
            assert result["document_unchanged"]
            assert [item["asset_id"] for item in result["items"]] == [formula.id, dependent.id]
            assert [item["fact"]["value"]["number"] for item in result["items"]] == [180, 0.56]
            repeated = checked(client.post(target, json=payload))
            assert all(item["reused"] for item in repeated["items"])
            assert [item["run"]["id"] for item in repeated["items"]] == [item["run"]["id"] for item in result["items"]]
            document = db.get(WritingDocument, article["id"])
            bundle = deepcopy(document.applicability)
            bundle["asset_bundle"]["assets"][0]["definition"]["payload"]["expression"] = "gap*100"
            document.applicability = bundle; db.commit()
            checked(client.post(target, json=payload), 409)
        db.expire_all()
        assert db.get(WritingDocumentVersion, article["current_version_id"]).content == []
