from copy import deepcopy

import pytest
from sqlalchemy import select

from tests.unit.test_writing_api import writing_client
from packages.platform.models import (
    Chunk, ChunkPolicy, Document, DocumentVersion, KnowledgeSpace, ProjectFact,
    WritingDocumentVersion, WritingGenerationRun, WritingProjectMaterial,
)
from packages.platform.writing_native import prioritize_native_evidence, validate_native_chapter


def packet():
    return {"section": {"key": "main", "title": "主要内容", "citation_required": False},
            "facts": [{"id": "available-v1", "fact_key": "available", "version": 1,
                       "value": {"number": 320}, "unit": "人"}],
            "computations": [], "evidence": [], "public_references": [],
            "graph": {"facts": [], "relations": []}}


def block(text="可用人员为320人。"):
    return {"id": "main-p1", "type": "p", "children": [{"text": text}]}


def binding():
    return {"block_id": "main-p1", "fact_ids": ["available-v1"], "occurrences": [
        {"leaf_path": [0], "start": 5, "end": 8, "source_type": "fact", "source_id": "available-v1"}]}


def test_native_evidence_page_prioritizes_direct_sources_and_is_bounded():
    available = [f"source-{index:02d}" for index in range(35)]
    result = prioritize_native_evidence(
        available, direct_dependency_ids={"source-34"}, limit=30,
    )
    assert result["ids"][0] == "source-34"
    assert len(result["ids"]) == 30
    assert result["page"] == {
        "selection": "direct_dependencies_first",
        "limit": 30,
        "total_available": 35,
        "requested_count": 35,
        "direct_dependency_count": 1,
        "candidate_count": 35,
        "returned_count": 30,
        "omitted_count": 5,
        "truncated": True,
        "direct_dependency_complete": True,
    }


def test_native_evidence_page_refuses_truncated_dependency_closure():
    available = [f"source-{index:02d}" for index in range(31)]
    with pytest.raises(ValueError, match="直接来源共有 31 条"):
        prioritize_native_evidence(
            available, direct_dependency_ids=set(available), limit=30,
        )


def test_native_numbers_get_stable_precise_bindings_without_model():
    content, bindings, quality = validate_native_chapter(packet(), [block()], [binding()], "work-1")
    assert quality["ok"]
    assert quality["review_required"]
    assert content[1]["children"][1]["text"] == "320"
    assert content[1]["children"][1]["writing_binding"]["fact_id"] == "available-v1"
    assert bindings[0]["metadata"]["input_keys"] == ["available"]
    assert bindings[0]["verification_status"] == "unverified"


def test_published_document_cannot_be_changed_by_normal_version_save():
    from packages.platform.models import WritingDocument
    with writing_client() as (client, db, _):
        _, article, _ = setup_article(client, db)
        row = db.get(WritingDocument, article["id"])
        row.status = "published"
        db.commit()
        before = row.current_version_id
        response = client.post(f"/api/v1/writing/documents/{row.id}/versions", json={
            "base_version_id": before, "request_id": "published-overwrite", "content": [block("新内容")]})
        assert response.status_code == 409
        db.refresh(row)
        assert row.status == "published"
        assert row.current_version_id == before


def test_native_paragraph_evidence_has_authorized_immutable_snapshots(monkeypatch):
    from packages.platform.models import (Chunk, ChunkPolicy, Document, DocumentVersion, KnowledgeSpace,
        WritingChunk, WritingChunkDependency)
    with writing_client() as (client, db, _):
        project, article, fact = setup_article(client, db)
        work = create_package(client, article)
        response = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
                               json=submit_payload(work, fact))
        assert response.status_code == 200, response.text
        space = db.scalar(select(KnowledgeSpace))
        source = Document(tenant_id=project["tenant_id"], space_id=space.id, title="已授权现场人员台账")
        policy = ChunkPolicy(tenant_id=project["tenant_id"], name="测试切片", config={})
        db.add_all([source, policy]); db.flush()
        source_version = DocumentVersion(tenant_id=project["tenant_id"], document_id=source.id,
            version_number=1, filename="台账.txt", content_type="text/plain", size=10,
            sha256="a" * 64, object_key="test/source", status="ready")
        db.add(source_version); db.flush()
        source.current_version_id = source_version.id
        source_chunk = Chunk(tenant_id=project["tenant_id"], space_id=space.id, document_id=source.id,
            version_id=source_version.id, chunk_policy_id=policy.id, chunk_id="source-stable-id",
            ordinal=0, text="可用人员320人。", content_hash="b" * 64, structural_path="paragraphs/0", status="published")
        db.add(source_chunk); db.flush()
        formal = db.scalar(select(WritingChunk).where(WritingChunk.chunk_id == "main-p1"))
        db.add(WritingChunkDependency(tenant_id=project["tenant_id"], project_id=project["id"],
            document_id=article["id"], writing_chunk_id=formal.id, binding_type="source_chunk",
            binding_id=source_chunk.id, binding_hash="b" * 64))
        computed = client.post(f"/api/v1/writing/projects/{project['id']}/compute", json={
            "operation": "quantity_amount", "inputs": {"quantity": 320, "unit_price": 320},
            "input_fact_ids": [fact.id], "input_fact_map": {"quantity": fact.id, "unit_price": fact.id},
            "output_fact_key": "test_computed", "output_label": "计算输出", "output_unit": "元"}).json()
        db.add(WritingChunkDependency(tenant_id=project["tenant_id"], project_id=project["id"],
            document_id=article["id"], writing_chunk_id=formal.id, binding_type="computation_run",
            binding_id=computed["id"], binding_hash="c" * 64))
        db.commit()
        url = f"/api/v1/writing/documents/{article['id']}/paragraph-evidence"
        response = client.get(url)
        assert response.status_code == 200, response.text
        deps = {row["type"]: row for row in response.json()["paragraphs"][0]["dependencies"]}
        assert deps["project_fact"]["snapshot"]["value"] == {"number": 320}
        assert deps["project_fact"]["snapshot"]["version"] == 1
        assert deps["source_chunk"]["snapshot"]["document_title"] == source.title
        assert deps["source_chunk"]["snapshot"]["text"] == source_chunk.text
        assert deps["source_chunk"]["snapshot"]["document_version_id"] == source_version.id
        assert deps["computation_run"]["snapshot"]["formula"]["expression"] == "quantity * unit_price"
        assert deps["computation_run"]["snapshot"]["result"]["value"] == 102400
        monkeypatch.setattr("apps.api.writing_semantics.has_space_permission", lambda *_: False)
        denied = client.get(url).json()["paragraphs"][0]["dependencies"]
        assert next(row for row in denied if row["type"] == "source_chunk")["snapshot"] is None


@pytest.mark.parametrize("text", ["可用人员为400人。", "可用人员为320人。"])
def test_native_unsupported_or_unbound_number_rejected(text):
    with pytest.raises(ValueError, match="数值"):
        validate_native_chapter(packet(), [block(text)], [], "work-1")


def test_native_forged_evidence_and_node_status_rejected():
    bad = binding()
    bad["evidence_ids"] = ["other-project-evidence"]
    with pytest.raises(ValueError, match="本次工作包"):
        validate_native_chapter(packet(), [block()], [bad], "work-1")
    with pytest.raises(ValueError, match="未声明字段"):
        validate_native_chapter(packet(), [{**block(), "verification_status": "verified"}], [binding()], "work-1")


def test_native_invalid_occurrence_and_duplicate_block_rejected():
    bad = binding()
    bad["occurrences"][0]["end"] = 7
    with pytest.raises(ValueError):
        validate_native_chapter(packet(), [block()], [bad], "work-1")
    with pytest.raises(ValueError, match="唯一稳定"):
        validate_native_chapter(packet(), [block(), block()], [], "work-1")


def setup_article(client, db):
    project = client.post("/api/v1/writing/projects", json={"name": "Native DSH 测试项目"}).json()
    article = client.post("/api/v1/writing/documents", json={"project_id": project["id"], "title": "资源报告", "content": []}).json()
    owner = project["owner_id"]
    fact = ProjectFact(tenant_id=project["tenant_id"], project_id=project["id"], fact_key="available",
        label="可用人员", fact_type="manual_input", value={"number": 320}, unit="人", source_type="manual_input",
        active=True, verification_status="verified", freshness_status="current", version=1, created_by=owner)
    db.add(fact)
    db.commit()
    return project, article, fact


def create_package(client, article, request_id="package-1"):
    response = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/work-package",
                           json={"section_key": "main", "request_id": request_id})
    assert response.status_code == 200, response.text
    return response.json()


def submit_payload(work, fact):
    spec = binding()
    spec["fact_ids"] = [fact.id]
    spec["occurrences"][0]["source_id"] = fact.id
    return {"checksum": work["checksum"], "request_id": "submit-1", "draft_blocks": [block()], "bindings": [spec]}


def test_work_package_closes_bindable_evidence_over_a_fact_after_first_page():
    with writing_client() as (client, db, _):
        project, article, fact = setup_article(client, db)
        space = db.scalar(select(KnowledgeSpace))
        policy = ChunkPolicy(tenant_id=project["tenant_id"], name="来源闭包切片", config={})
        source = Document(
            tenant_id=project["tenant_id"], space_id=space.id, title="投资估算来源表",
        )
        db.add_all([policy, source])
        db.flush()
        version = DocumentVersion(
            tenant_id=project["tenant_id"], document_id=source.id, version_number=1,
            filename="投资估算来源表.txt", content_type="text/plain", size=100,
            sha256="d" * 64, object_key="test/investment-source", status="ready",
        )
        db.add(version)
        db.flush()
        source.current_version_id = version.id
        chunks = []
        for ordinal in range(35):
            text = "可用人员320人。" if ordinal == 34 else f"补充材料第{ordinal + 1}条。"
            chunks.append(Chunk(
                tenant_id=project["tenant_id"], space_id=space.id, document_id=source.id,
                version_id=version.id, chunk_policy_id=policy.id,
                chunk_id=f"source-closure-{ordinal:02d}", ordinal=ordinal, text=text,
                content_hash=(f"{ordinal:064d}"[-64:]), structural_path=f"paragraphs/{ordinal}",
                status="published",
            ))
        db.add_all(chunks)
        db.flush()
        direct = chunks[-1]
        db.add(WritingProjectMaterial(
            tenant_id=project["tenant_id"], project_id=project["id"],
            document_id=source.id, version_id=version.id, material_role="task_data",
            usage_scope="task_only", status="active", added_by=project["owner_id"],
        ))
        fact.source_type = "manual_input"
        fact.source_id = direct.id
        fact.source_version = version.id
        fact.source_locator = {
            "chunk_id": direct.id, "evidence_ids": [direct.id],
            "document_version_id": version.id,
        }
        db.commit()

        work = create_package(client, article, request_id="source-closure-package")
        evidence_ids = [item["id"] for item in work["evidence"]]
        inventory_ids = [item["id"] for item in work["source_inventory"]]
        assert evidence_ids[0] == direct.id
        assert evidence_ids == inventory_ids
        assert len(evidence_ids) == 30
        assert work["evidence"][0]["dependency_required"] is True
        assert work["source_inventory_page"]["direct_dependency_complete"] is True
        assert work["source_inventory_page"]["truncated"] is True
        assert work["sources_omitted"] == 5

        payload = submit_payload(work, fact)
        payload["bindings"][0]["evidence_ids"] = [direct.id]
        submitted = client.post(
            f"/api/v1/writing/documents/{article['id']}/native-chapters/"
            f"{work['work_package_id']}/submit",
            json=payload,
        )
        assert submitted.status_code == 200, submitted.text


def test_native_api_creates_version_no_inner_agent_and_idempotent_retry():
    with writing_client() as (client, db, _):
        _, article, fact = setup_article(client, db)
        work = create_package(client, article)
        assert create_package(client, article)["work_package_id"] == work["work_package_id"]
        response = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
                               json=submit_payload(work, fact))
        assert response.status_code == 200, response.text
        version = response.json()["document_version"]
        assert version["version"] == 2
        run = db.get(WritingGenerationRun, work["work_package_id"])
        assert run.agent_session_id is None and run.assistant_message_id is None
        response2 = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
                                json=submit_payload(work, fact))
        assert response2.status_code == 200 and response2.json()["unchanged"]
        assert response2.json()["document_version"]["id"] == version["id"]
        assert db.get(WritingDocumentVersion, article["current_version_id"]).content == []


def test_native_chapter_submit_is_atomic_and_failed_attempt_can_retry_same_package():
    """A rejected draft is not a partial chapter and must not advance history."""
    with writing_client() as (client, db, _):
        _, article, fact = setup_article(client, db)
        initial_version_id = article["current_version_id"]
        work = create_package(client, article, "atomic-package")
        guidance = "\n".join(work["instructions"])
        assert "3—5 个顶层正文块" in guidance
        assert "表格本身" in guidance and "单元格内 p" in guidance
        assert "逐叶计算" in guidance
        assert "只能取自本工作包" in guidance
        invalid = submit_payload(work, fact)
        invalid["request_id"] = "invalid-submit"
        invalid["bindings"] = []
        rejected = client.post(
            f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
            json=invalid,
        )
        assert rejected.status_code == 422, rejected.text
        current = client.get(f"/api/v1/writing/documents/{article['id']}").json()
        assert current["current_version_id"] == initial_version_id
        assert db.get(WritingGenerationRun, work["work_package_id"]).status == "awaiting_native"

        complete = submit_payload(work, fact)
        complete["request_id"] = "complete-submit"
        accepted = client.post(
            f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
            json=complete,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["document_version"]["version"] == 2
        assert db.get(WritingDocumentVersion, initial_version_id).content == []


def test_fresh_package_cannot_append_or_replace_an_already_submitted_section():
    """Native section submission is one-shot; later replacement uses editor suggestions.

    A fresh work package intentionally does not turn a previously accepted partial
    section into an append or an implicit replacement.  This keeps author edits and
    immutable history safe while still allowing failed (uncommitted) attempts to
    retry the original package.
    """
    with writing_client() as (client, db, _):
        _, article, fact = setup_article(client, db)
        initial_version_id = article["current_version_id"]
        partial_work = create_package(client, article, "partial-package")
        partial_payload = submit_payload(partial_work, fact)
        partial_payload["request_id"] = "partial-submit"
        partial = client.post(
            f"/api/v1/writing/documents/{article['id']}/native-chapters/{partial_work['work_package_id']}/submit",
            json=partial_payload,
        )
        assert partial.status_code == 200, partial.text
        accepted_version = partial.json()["document_version"]

        fresh = create_package(client, {**article, "current_version_id": accepted_version["id"]}, "complete-package")
        complete_block = block("经核对，可用人员为320人，章节内容现已完整。")
        complete_block["id"] = "main-complete-p1"
        complete_binding = binding()
        complete_binding["block_id"] = complete_block["id"]
        complete_binding["fact_ids"] = [fact.id]
        complete_binding["occurrences"][0].update({
            "leaf_path": [0], "start": 9, "end": 12, "source_id": fact.id,
        })
        rejected = client.post(
            f"/api/v1/writing/documents/{article['id']}/native-chapters/{fresh['work_package_id']}/submit",
            json={
                "checksum": fresh["checksum"],
                "request_id": "complete-resubmit",
                "draft_blocks": [complete_block],
                "bindings": [complete_binding],
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert "本章已存在" in rejected.text

        current = client.get(f"/api/v1/writing/documents/{article['id']}").json()
        assert current["current_version_id"] == accepted_version["id"]
        content = db.get(WritingDocumentVersion, accepted_version["id"]).content
        assert [node.get("type") for node in content].count("h2") == 1
        assert [node.get("id") for node in content] == [f"native-{partial_work['work_package_id']}-heading", "main-p1"]
        assert db.get(WritingDocumentVersion, initial_version_id).content == []
        assert len(list(db.scalars(select(WritingDocumentVersion).where(
            WritingDocumentVersion.document_id == article["id"]
        )))) == 2
        assert db.get(WritingGenerationRun, fresh["work_package_id"]).status == "awaiting_native"


def test_new_blank_document_in_same_project_does_not_mutate_partially_written_document():
    with writing_client() as (client, db, _):
        project, original, fact = setup_article(client, db)
        work = create_package(client, original, "original-package")
        written = client.post(
            f"/api/v1/writing/documents/{original['id']}/native-chapters/{work['work_package_id']}/submit",
            json=submit_payload(work, fact),
        )
        assert written.status_code == 200, written.text
        original_version = written.json()["document_version"]
        original_content = deepcopy(original_version["content"])

        created = client.post("/api/v1/writing/documents", json={
            "project_id": project["id"],
            "title": "资源报告（原生重写稿）",
            "document_type": "feasibility_report",
            "purpose": "形成独立讨论稿",
            "audience": "项目决策人员",
            "writing_requirements": "按章生成并绑定依据。",
            "content": [],
        })
        assert created.status_code == 200, created.text
        blank = created.json()
        assert blank["id"] != original["id"]
        assert blank["project_id"] == project["id"]
        assert blank["current_version"]["version"] == 1
        assert blank["current_version"]["content"] == []

        unchanged = client.get(f"/api/v1/writing/documents/{original['id']}").json()
        assert unchanged["current_version_id"] == original_version["id"]
        assert unchanged["current_version"]["content"] == original_content
        assert db.get(WritingDocumentVersion, original["current_version_id"]).content == []
        documents = client.get(f"/api/v1/writing/projects/{project['id']}/documents").json()
        assert {row["id"] for row in documents} == {original["id"], blank["id"]}


def test_native_api_changed_inputs_and_baseline_fail_closed():
    with writing_client() as (client, db, _):
        _, article, fact = setup_article(client, db)
        work = create_package(client, article)
        fact.value = {"number": 400}
        db.commit()
        response = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
                               json=submit_payload(work, fact))
        assert response.status_code == 409, response.text
        current = client.get(f"/api/v1/writing/documents/{article['id']}").json()
        assert current["current_version_id"] == article["current_version_id"]


def test_version_compare_and_swap_and_request_idempotency():
    with writing_client() as (client, db, _):
        _, article, _ = setup_article(client, db)
        payload = {"content": [block("正文修改。")], "base_version_id": article["current_version_id"], "request_id": "save-12345"}
        url = f"/api/v1/writing/documents/{article['id']}/versions"
        first = client.post(url, json=payload)
        assert first.status_code == 200, first.text
        retry = client.post(url, json=payload)
        assert retry.status_code == 200 and retry.json()["unchanged"]
        conflict = client.post(url, json={**payload, "request_id": "save-54321", "content": [block("另一个窗口修改。")]})
        assert conflict.status_code == 409
        forged_retry = client.post(url, json={**payload, "content": [block("覆盖。")]})
        assert forged_retry.status_code == 409


def test_normal_save_rejects_controlled_number_edit_and_publish_needs_review():
    with writing_client() as (client, db, _):
        _, article, fact = setup_article(client, db)
        work = create_package(client, article)
        response = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
                               json=submit_payload(work, fact))
        assert response.status_code == 200, response.text
        version = response.json()["document_version"]
        changed = deepcopy(version["content"])
        changed[1]["children"][1]["text"] = "999"
        url = f"/api/v1/writing/documents/{article['id']}/versions"
        invalid = client.post(url, json={"base_version_id": version["id"], "content": changed})
        assert invalid.status_code == 422, invalid.text
        final = client.post(url, json={"base_version_id": version["id"], "content": version["content"], "publish": True})
        assert final.status_code == 409, final.text


def test_native_outline_is_stored_with_cas_and_visible_to_chapter_package():
    with writing_client() as (client, db, _):
        _, article, _ = setup_article(client, db)
        url = f"/api/v1/writing/documents/{article['id']}/native-outline"
        payload = {"base_version_id": article["current_version_id"], "request_id": "outline-123",
            "sections": [{"key": "investment", "title": "投资估算", "instruction": "按真实投资台账论述。",
                          "citation_required": False}]}
        response = client.put(url, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["base_version_id"] != article["current_version_id"]
        retry = client.put(url, json=payload)
        assert retry.status_code == 200 and retry.json()["unchanged"]
        assert client.put(url, json={**payload, "request_id": "outline-new"}).status_code == 409
        package = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/work-package",
            json={"section_key": "investment", "request_id": "package-investment"})
        assert package.status_code == 200, package.text
        assert package.json()["section"]["title"] == "投资估算"
        assert package.json()["base_version_id"] == response.json()["base_version_id"]


def test_native_partial_impact_keeps_unselected_and_rebinds_selected_fact_version():
    with writing_client() as (client, db, _):
        project, article, fact = setup_article(client, db)
        work = create_package(client, article)
        payload = submit_payload(work, fact)
        payload["draft_blocks"].append({"id": "main-p2", "type": "p", "children": [{"text": "现有人员为320人。"}]})
        second = deepcopy(payload["bindings"][0])
        second["block_id"] = "main-p2"
        payload["bindings"].append(second)
        submitted = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit", json=payload)
        assert submitted.status_code == 200, submitted.text
        version = submitted.json()["document_version"]
        preview = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/preview", json={
            "document_id": article["id"], "changes": [{"fact_key": "available", "new_value": {"number": 400}, "reason": "人员台账更新"}]})
        assert preview.status_code == 200, preview.text
        proposals = preview.json()["impact"]["content_proposals"]
        assert {p["block_id"] for p in proposals if p["selectable"]} == {"main-p1", "main-p2"}
        before = client.get(f"/api/v1/writing/documents/{article['id']}").json()
        assert before["current_version_id"] == version["id"]
        apply_payload = {"preview_id": preview.json()["id"], "accepted_block_ids": ["main-p1"]}
        applied = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json=apply_payload)
        assert applied.status_code == 200, applied.text
        nodes = {n["id"]: n for n in applied.json()["document_version"]["content"]}
        changed_leaf = nodes["main-p1"]["children"][1]
        assert changed_leaf["text"] == "400"
        assert changed_leaf["writing_binding"]["fact_id"] != fact.id
        assert changed_leaf["writing_binding"]["fact_version"] == 2
        assert nodes["main-p2"]["children"][1]["text"] == "320"
        assert nodes["main-p2"]["freshness_status"] == "stale"
        assert db.get(WritingDocumentVersion, version["id"]).content[1]["children"][1]["text"] == "320"
        retry = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json=apply_payload)
        assert retry.status_code == 200 and retry.json()["unchanged"]


def test_impact_rejects_any_other_authority_change_after_preview():
    with writing_client() as (client, db, _):
        project, article, fact = setup_article(client, db)
        work = create_package(client, article)
        submitted = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit",
                               json=submit_payload(work, fact))
        assert submitted.status_code == 200, submitted.text
        preview = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/preview", json={
            "document_id": article["id"], "changes": [{"fact_key": "available", "new_value": {"number": 400}, "reason": "人员台账更新"}]})
        assert preview.status_code == 200, preview.text
        extra = ProjectFact(tenant_id=project["tenant_id"], project_id=project["id"], fact_key="another_input",
            label="需求人数", fact_type="manual_input", value={"number": 500}, unit="人", source_type="manual_input",
            active=True, verification_status="verified", freshness_status="current", version=1, created_by=project["owner_id"])
        db.add(extra)
        db.commit()
        applied = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json={
            "preview_id": preview.json()["id"], "accepted_block_ids": ["main-p1"]})
        assert applied.status_code == 409, applied.text


def test_native_table_occurrences_do_not_update_another_equal_value():
    with writing_client() as (client, db, _):
        project, article, fact = setup_article(client, db)
        other = ProjectFact(tenant_id=project["tenant_id"], project_id=project["id"], fact_key="beds",
            label="可用床位", fact_type="manual_input", value={"number": 320}, unit="张", source_type="manual_input",
            active=True, verification_status="verified", freshness_status="current", version=1, created_by=project["owner_id"])
        db.add(other)
        db.commit()
        work = create_package(client, article)
        table = {"id": "resources", "type": "table", "children": [{"id": "row1", "type": "tr", "children": [
            {"id": "cell1", "type": "td", "children": [{"id": "cell1p", "type": "p", "children": [{"text": "320人"}]}]},
            {"id": "cell2", "type": "td", "children": [{"id": "cell2p", "type": "p", "children": [{"text": "320张"}]}]},
        ]}]}
        payload = {"checksum": work["checksum"], "request_id": "table-submit", "draft_blocks": [table], "bindings": [
            {"block_id": "resources", "fact_ids": [fact.id, other.id], "occurrences": [
                {"leaf_path": [0, 0, 0, 0], "start": 0, "end": 3, "source_type": "fact", "source_id": fact.id},
                {"leaf_path": [0, 1, 0, 0], "start": 0, "end": 3, "source_type": "fact", "source_id": other.id},
            ]}]}
        response = client.post(f"/api/v1/writing/documents/{article['id']}/native-chapters/{work['work_package_id']}/submit", json=payload)
        assert response.status_code == 200, response.text
        preview = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/preview", json={
            "document_id": article["id"], "changes": [{"fact_key": "available", "new_value": {"number": 400}, "reason": "人员台账更新"}]})
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["impact"]["content_proposals"]) == 1
        assert preview.json()["impact"]["content_proposals"][0]["selectable"]
        applied = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json={
            "preview_id": preview.json()["id"], "accepted_block_ids": ["resources"]})
        assert applied.status_code == 200, applied.text
        changed_table = applied.json()["document_version"]["content"][1]
        assert changed_table["children"][0]["children"][0]["children"][0]["children"][0]["text"] == "400"
        assert changed_table["children"][0]["children"][1]["children"][0]["children"][0]["text"] == "320"
