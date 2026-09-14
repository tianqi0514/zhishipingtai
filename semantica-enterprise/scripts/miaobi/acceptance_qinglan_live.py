"""Opt-in live acceptance in one named test space; never resets existing data.

Run with MIAOBI_DEMO_URL and a password on stdin (never persisted). Stage
artifacts contain only this synthetic exercise, public object IDs and evidence.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import time
from collections import Counter

from scripts.miaobi.demo_client import DemoClient
from scripts.miaobi.acceptance_upload_to_report import parse_sse

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / "demo/miaobi/qinglan-live-20260914"
OUT = ROOT / ".demo-build/qinglan-live-20260914"
CODE = "miaobi-qinglan-live-20260914"


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def emit(name, value):
    print(json.dumps({"stage": name, "result": value}, ensure_ascii=False), flush=True)


def require_confirmed_export_gates(gates):
    """Automated export verifies a human decision; it must never make one."""
    if any(gate.get("status") != "confirmed" for gate in gates):
        raise RuntimeError("请先在妙笔界面核对来源与口径，并由业务人员完成待确认节点；导出测试不会代替人工确认")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["preflight", "status", "upload", "setup", "generate", "diagnose", "inspect", "export", "change-preview"])
    args = parser.parse_args()
    if not os.environ.get("MIAOBI_DEMO_PASSWORD"):
        print("Authentication input required on stdin", flush=True)
        os.environ["MIAOBI_DEMO_PASSWORD"] = sys.stdin.readline().strip()
    api = DemoClient()
    state_path = OUT / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    space = api.one("/spaces", "code", CODE)
    if not space:
        raise RuntimeError("Create the dedicated space through the browser first")
    state["space"] = space
    sid = space["id"]
    if not state_path.exists():
        save("state", state)
    if args.stage == "status":
        documents = api.get("/documents", space_id=sid)
        info = []
        for document in documents:
            detail = api.get(f"/documents/{document['id']}")
            current = next(v for v in detail["versions"] if v["id"] == document["current_version_id"])
            summary = current.get("parse_summary") or {}
            info.append({"title": document["title"], "version_id": current["id"], "status": current["status"], "summary": summary})
        save("material_status", info)
        emit("material_status", info)
        vids = {v["version_id"] for v in info}
        jobs = [j for j in api.get("/jobs") if (j.get("input") or {}).get("version_id") in vids]
        emit("jobs", [{k:j.get(k) for k in ["id", "job_type", "status", "progress", "stage", "error", "updated_at"]} for j in jobs])
    elif args.stage == "preflight":
        readiness = api.get("/processing/readiness")
        save("readiness", readiness)
        assert readiness.get("ready"), "Processing preflight is not ready"
        # Do not persist model objects: even encrypted credential fields do not
        # belong in an acceptance report. Only publish test results.
        models = api.get("/model-configs")
        selected = [m for m in models if m.get("enabled") and m.get("model_kind") == "llm" and ("deepseek" in str(m.get("model_name", "")).lower() or "FP8" in str(m.get("model_name", "")))]
        assert selected, "No eligible live LLM configuration"
        results = []
        for model in selected:
            started = time.monotonic()
            result = api.post(f"/model-configs/{model['id']}/test")
            results.append({"name": model.get("name"), "model": model.get("model_name"), "elapsed_seconds": round(time.monotonic()-started, 2), "result": result})
        save("model_checks", results)
        emit("preflight", {"space_id": sid, "ready": readiness.get("ready"), "models": results})
        assert all(r["result"]["status"] == "success" for r in results)
    elif args.stage == "upload":
        import hashlib

        files = sorted((MATERIALS / "01-上传材料").glob("*"))
        assert len(files) == 4, [p.name for p in files]
        assert all(path.is_file() for path in files), "Upload inputs must be files"
        uploaded = state.setdefault("uploaded", [])
        for path in files:
            emit("upload_start", path.name)
            record = next((r for r in uploaded if r["filename"] == path.name), None)
            if record:
                continue
            existing = next((d for d in api.get("/documents", space_id=sid) if d["title"] == path.name), None)
            start = time.monotonic()
            if existing:
                detail = api.get(f"/documents/{existing['id']}")
                version = next(v for v in detail["versions"] if v["id"] == existing["current_version_id"])
                api.wait_knowledge_job(version["id"])
                result = {"document": existing, "version": version}
            else:
                result = api.upload_file(sid, path, mode="both")
            record = {"filename": path.name, "document": result["document"], "version": result["version"], "seconds": round(time.monotonic()-start, 2)}
            uploaded.append(record)
            save("state", state)
            emit("uploaded", {"file": path.name, "seconds": record["seconds"]})
        assert len(uploaded) == len(files) and {r["filename"] for r in uploaded} == {p.name for p in files}, "Upload state does not match this exercise's four materials"
        chunks, material_checks = {}, []
        for r in uploaded:
            vid = r["version"]["id"]
            detail = api.get(f"/documents/{r['document']['id']}")
            assert detail["space_id"] == sid and detail["current_version_id"] == vid, "Material space or pinned version changed"
            current = next(v for v in detail["versions"] if v["id"] == vid)
            summary = current.get("parse_summary") or {}
            assert current["status"] == "ready" and summary.get("knowledge_status") == "published", f"Material is not ready and published: {r['filename']}"
            assert {"vector", "graph"}.issubset(summary.get("knowledge_targets_completed") or []), f"Material did not complete both knowledge channels: {r['filename']}"
            checksum = hashlib.sha256((MATERIALS / "01-上传材料" / r["filename"]).read_bytes()).hexdigest()
            assert current["sha256"] == checksum, f"Uploaded content differs from local material: {r['filename']}"
            rows, offset = [], 0
            while True:
                page = api.get(f"/versions/{vid}/chunks", offset=offset, limit=200)
                rows.extend(page["items"])
                offset += len(page["items"])
                if offset >= page["total"]:
                    break
                assert page["items"], "Chunk pagination made no progress"
            assert rows and len({c["id"] for c in rows}) == len(rows), r["filename"]
            assert all(c["document_id"] == detail["id"] and c["version_id"] == vid and c["space_id"] == sid and c["text"].strip() for c in rows), "Chunk identity or text does not match uploaded material"
            prefix = r["filename"][:2]
            assert prefix not in chunks, "Material filename prefixes must be unique"
            chunks[prefix] = rows
            r["version"] = current
            material_checks.append({"filename": r["filename"], "document_id": detail["id"], "version_id": vid, "sha256": checksum, "status": current["status"], "knowledge_status": summary["knowledge_status"], "completed_channels": sorted(summary["knowledge_targets_completed"]), "chunk_count": len(rows)})
        save("state", state)
        save("material_checks", material_checks)
        save("chunks", chunks)
        emit("chunks", {k: len(v) for k, v in chunks.items()})
    elif args.stage == "setup":
        chunks = json.loads((OUT / "chunks.json").read_text())
        def evidence(file, *words):
            matches = [c for c in chunks[file] if all(w in c["text"] for w in words)]
            if not matches:
                raise RuntimeError(f"Actual source fragment missing {file} {words}")
            return max(matches, key=lambda c: len(c["text"]))
        def once(key, create):
            if key not in state:
                state[key] = create()
                save("state", state)
            return state[key]
        def wait_mutation(result):
            job_id = result.get("job_id") or (result.get("job") or {}).get("id")
            if job_id:
                api.wait_job(job_id)
            return result
        existing_entities = api.get("/knowledge/entities", space_id=sid, limit=500)["items"]
        existing_facts = api.get("/knowledge/facts", space_id=sid, limit=500)["items"]
        if not (OUT / "automatic_extraction.json").exists():
            save("automatic_extraction", {"captured_before_curation": True, "entities": existing_entities, "facts": existing_facts})
        labels = [("云桥体育馆安置点", "安置点"), ("柳溪供水站", "供水设施"), ("供水中断", "风险事件"), ("县生活保障组", "责任单位"), ("城北中学安置点", "安置点"), ("城北备用水井", "供水设施")]
        entities = {}
        for label, kind in labels:
            def create_entity(label=label, kind=kind):
                old = next((e for e in existing_entities if e["canonical_name"] == label), None)
                if old:
                    wait_mutation(api.put(f"/knowledge/entities/{old['id']}", {"entity_type": kind, "status": "published", "reason_note": "仅本次演练：按上传业务材料核验标准对象类型"}))
                    return old
                return api.post("/knowledge/entities", {"space_id": sid, "canonical_name": label, "entity_type": kind, "status": "published", "confidence": 1})
            entities[label] = once("entity:"+label, create_entity)
        # Same-name objects from different paragraphs were extracted with
        # incompatible generic types. The fixture's names are unambiguous;
        # merge ONLY those six reviewed labels, with reversible audit records.
        merged_now = False
        for label, _ in labels:
            winner = entities[label]
            for duplicate in existing_entities:
                if duplicate["canonical_name"] != label or duplicate["id"] == winner["id"] or duplicate.get("status") != "published":
                    continue
                key = "merge:"+duplicate["id"]
                if key not in state:
                    if "release" in state:
                        raise RuntimeError("Cannot change graph after pinning the writing Release; prepare a new release explicitly")
                    state[key] = wait_mutation(api.post("/curation/entities/pair", {"space_id": sid, "left_entity_id": winner["id"], "right_entity_id": duplicate["id"], "winner_entity_id": winner["id"], "operation": "merge", "reason_note": "本次演练按原文逐项核验：同一全称被抽成多个通用类型，保留标准节点及所有来源；不是跨名称自动合并"}))
                    merged_now = True
                    save("state", state)
        existing_facts = api.get("/knowledge/facts", space_id=sid, limit=500, include_inferred=False)["items"]
        specs = [("云桥体育馆安置点", "依赖", "柳溪供水站", "01"), ("柳溪供水站", "发生", "供水中断", "02"), ("县生活保障组", "负责", "云桥体育馆安置点", "03"), ("城北中学安置点", "依赖", "城北备用水井", "01")]
        for subject, predicate, obj, file in specs:
            source = evidence(file, subject, obj)
            def create_fact(subject=subject, predicate=predicate, obj=obj, source=source):
                old = next((f for f in existing_facts if f["subject_entity_id"] == entities[subject]["id"] and f["predicate"] == predicate and f.get("object_entity_id") == entities[obj]["id"]), None)
                if old:
                    wait_mutation(api.put(f"/knowledge/facts/{old['id']}", {"source_chunk_id": source["id"], "status": "published", "reason_note": "已逐字核对本次演练上传原文，人工确认来源，不冒充自动抽取"}))
                    return old
                return api.post("/knowledge/facts", {"space_id": sid, "subject_entity_id": entities[subject]["id"], "predicate": predicate, "object_entity_id": entities[obj]["id"], "source_chunk_id": source["id"], "status": "published", "confidence": 1})
            once("fact:"+subject+predicate+obj, create_fact)
        if merged_now:
            state["graph_release"] = api.post(f"/knowledge/releases/publish?space_id={sid}")
            save("state", state)
        else:
            once("graph_release", lambda: api.post(f"/knowledge/releases/publish?space_id={sid}"))
        ontology = once("ontology", lambda: api.post("/ontologies", {"space_id": sid, "code": CODE+"-ontology", "name": "青岚安置保障语义模型·实测0914", "namespace": "urn:qinglan:20260914:"}))
        if "ontology_version" not in state:
            api.post(f"/ontologies/{ontology['id']}/suggestions/generate")
            suggestions = api.get(f"/ontologies/{ontology['id']}/suggestions")
            accepted = []
            for candidate in suggestions:
                if candidate["label"] in {"安置点", "供水设施", "风险事件", "责任单位", "依赖", "发生", "负责"} and candidate["status"] != "published":
                    api.put(f"/ontologies/{ontology['id']}/suggestions/{candidate['id']}", {"decision": "accept", "definition": "据本次已上传并人工核验的演练材料生成，范围限于安置保障写作"})
                    accepted.append(candidate["label"])
            assert {"依赖", "发生", "负责", "安置点"}.issubset(accepted), accepted
            state["ontology_version"] = api.post(f"/ontologies/{ontology['id']}/publish")["version"]
            save("state", state)
        ruleset = once("ruleset", lambda: api.post("/analysis/rule-sets", {"name": "青岚安置保障影响规则·实测0914", "space_ids": [sid]}))
        rule = once("rule", lambda: api.post(f"/analysis/rule-sets/{ruleset['id']}/rules", {"name": "依赖设施供水中断影响安置点", "definition": {"conditions": [{"subject": "X", "predicate": "依赖", "object": "Y"}, {"subject": "Y", "predicate": "发生", "object": "Z"}], "conclusion": {"subject": "X", "predicate": "受到影响", "object": "Z"}}}))
        version = api.get(f"/analysis/rules/{rule['id']}/versions")[0]
        product = once("product", lambda: api.post("/knowledge-products", {"code": CODE+"-product", "name": "青岚安置保障知识供给·实测0914", "status": "active", "space_ids": [sid]}))
        release = once("release", lambda: api.post(f"/knowledge-products/{product['id']}/releases", {"note": "首轮4份真实上传的合成业务材料及人工核验关系，非真实灾情"}))
        package = once("package", lambda: api.post("/writing/scenario-packages", {"code": CODE+"-scenario", "name": "安置保障与搜救协调报告·青岚实测", "disaster_type": "earthquake", "description": "虚构演练，资料先核验，业务规则与数值计算分别形成依据，不承担真实指挥决定。"}))
        config = json.loads((MATERIALS / "03-管理员预置配置/写作业务配置.template.json").read_text())
        config["sections"][1]["knowledge"]["ontology_version_id"] = state["ontology_version"]["id"]
        config["sections"][1]["knowledge"]["rule_version_ids"] = [version["id"]]
        for section in config["sections"]:
            section["purpose"] = section["purpose"].replace("安置点A与B", "云桥体育馆安置点与城北中学安置点")
            for before, after in [("安置点A", "云桥体育馆安置点"), ("安置点B", "城北中学安置点"), ("供水站C", "柳溪供水站"), ("保障组D", "县生活保障组")]:
                section["purpose"] = section["purpose"].replace(before, after)
        config["sections"][2]["purpose"] += " 县域搜救力量与安置点生活保障是不同统计范围，不把500人或180人表述为某安置点的服务人数。"
        save("business_config", config)
        scenario = once("scenario", lambda: api.put(f"/writing/scenario-packages/{package['id']}/business-config", config)["version"])
        project = once("project", lambda: api.post("/writing/projects", {"code": CODE+"-report", "name": "青岚县安置保障与搜救协调报告（演练）", "scenario_package_version_id": scenario["id"], "knowledge_product_release_id": release["id"]}))
        pid = project["id"]
        for record in state["uploaded"]:
            once("material:"+record["filename"], lambda r=record: api.post(f"/writing/projects/{pid}/materials", {"document_id": r["document"]["id"], "version_id": r["version"]["id"], "material_role": "policy_basis" if r["filename"].startswith("03") else "task_data"}))
        source = evidence("04", "500", "320")
        for key, label, value in [("rescue_required", "搜救人员需求", 500), ("rescue_available", "可用搜救人员", 320)]:
            fact = once(key, lambda k=key,l=label,v=value: api.post(f"/writing/projects/{pid}/facts", {"fact_key": k, "label": l, "fact_type": "manual_input", "value": {"number": v}, "unit": "人", "source_type": "manual_input", "source_id": source["id"], "source_version": source["version_id"], "source_locator": {"document_id": source["document_id"], "chunk_id": source["id"], "structural_path": source.get("structural_path"), "data_time": "2026-09-14T09:00:00+08:00"}}))
            once(key+":confirmed", lambda f=fact: api.post(f"/writing/projects/{pid}/facts/{f['id']}/confirm", {"decision": "confirm", "reason": "已核对04号县域搜救清单09:00需求与到位口径；演练验证，不代表实际调度"}))
        prepared = once("chapter_evidence", lambda: api.post(f"/writing/projects/{pid}/chapter-evidence/preview"))
        derived = [r for r in prepared["references"].values() if r["kind"] == "inference"]
        assert any(r["text"] == "云桥体育馆安置点 — 受到影响 → 供水中断" for r in derived), derived
        assert not any("城北中学安置点" in r["text"] for r in derived), derived
        once("evidence_applied", lambda: api.post(f"/writing/projects/{pid}/chapter-evidence/{prepared['run_id']}/apply"))
        retrieval = {}
        for channel in ["keyword", "vector", "graph"]:
            result = api.post("/search", {"query": "柳溪供水站 供水中断 云桥体育馆安置点", "space_ids": [sid], "top_k": 10, **{f"use_{c}": c == channel for c in ["keyword", "vector", "graph"]}, "use_reranker": False})
            retrieval[channel] = result
            assert result["items"], f"{channel} returned no evidence"
        save("retrieval", retrieval)
        emit("setup", {"project_id": pid, "inferences": [r["text"] for r in derived], "retrieval": {c: len(r["items"]) for c,r in retrieval.items()}})
    elif args.stage == "diagnose":
        runs = api.get(f"/writing/projects/{state['project']['id']}/generation-runs")
        summaries = []
        for item in runs:
            run = api.get(f"/writing/generation-runs/{item['id']}")
            summaries.append({k: run.get(k) for k in ["id", "status", "stage", "progress", "error", "quality_report", "document_id", "created_at", "updated_at"]})
            conversation_id = state.get("generation_conversation_ids", {}).get(item["id"])
            if conversation_id:
                conversation = api.get(f"/conversations/{conversation_id}")
                answer = next((m for m in conversation.get("messages", []) if m["id"] == run.get("assistant_message_id")), {})
                save("agent_answer_"+item["id"], {"status": answer.get("status"), "content": answer.get("content"), "event_types": dict(Counter(e["event_type"] for e in conversation.get("events", [])))})
        save("generation_diagnostics", summaries)
        emit("diagnostics", summaries)
    elif args.stage == "generate":
        pid = state["project"]["id"]
        # Recover a successful POST whose response was lost before state.json
        # was written. Only this dedicated project's runs are candidates.
        if "generation" not in state:
            existing_runs = api.get(f"/writing/projects/{pid}/generation-runs")
            state["generation"] = existing_runs[0] if existing_runs else api.post(f"/writing/projects/{pid}/generate-report", {})
            save("state", state)
        run = api.get(f"/writing/generation-runs/{state['generation']['id']}")
        assert run["project_id"] == pid, "Generation run belongs to another project"
        if run["status"] in {"quality_failed", "agent_failed", "cancelled"}:
            latest = api.get(f"/writing/projects/{pid}/generation-runs")
            if latest and latest[0]["id"] != run["id"]:
                run = latest[0]
        if run["status"] in {"quality_failed", "agent_failed", "cancelled"}:
            # An explicit new invocation may retry a terminal failure. Keep
            # its audit history; never overwrite or cancel an active attempt.
            history = state.setdefault("previous_generation_ids", [])
            if run["id"] not in history:
                history.append(run["id"])
            save("state", state)
            run = api.post(f"/writing/projects/{pid}/generate-report", {})
        state["generation"] = run
        save("state", state)
        emit("generation_started", {"id": run["id"], "status": run["status"]})
        browser_owned_run = run["status"] == "agent_running"
        started = time.monotonic()
        streamed_events = []
        if run["status"] == "awaiting_agent":
            with api.client.stream("POST", f"/writing/generation-runs/{run['id']}/agent", headers={"Accept": "text/event-stream"}) as response:
                api._raise(response)
                streamed_events = parse_sse(response)
            assert not any(n in {"turn_failed", "turn_cancelled"} for n, _ in streamed_events), "Agent did not complete; rerun generate to recover from protected platform history"
        elif run["status"] not in {"agent_running", "completed"}:
            raise RuntimeError(f"Unsupported generation status: {run['status']}")

        # Agent completion is persisted separately from the run status. A
        # completed turn can still leave the run agent_running until finalize.
        deadline = time.monotonic() + 1800
        last_notice = 0.0
        while True:
            run = api.get(f"/writing/generation-runs/{run['id']}")
            assert run["project_id"] == pid
            if run["status"] in {"quality_failed", "agent_failed", "cancelled"}:
                raise RuntimeError("Generation failed or was cancelled; protected history is retained. Rerun generate for a new attempt.")
            session_id = run.get("agent_session_id") or (run.get("agent_session") or {}).get("id")
            assert session_id and run.get("assistant_message_id"), "Generation lacks its persisted Agent turn"
            # The writing Agent-session read route accepts editing sessions
            # only. Resolve this report_generation conversation through its
            # existing public list, keeping unrelated summaries out of output.
            conversation_ids = state.setdefault("generation_conversation_ids", {})
            if run["id"] not in conversation_ids:
                offset, matching = 0, []
                while True:
                    candidates = api.get("/conversations", offset=offset, limit=100)["items"]
                    matching = [c for c in candidates if (c.get("settings") or {}).get("writing_session_id") == session_id and (c.get("settings") or {}).get("writing_project_id") == pid]
                    if matching or len(candidates) < 100:
                        break
                    offset += len(candidates)
                assert len(matching) == 1, "Generation conversation mapping is unavailable or ambiguous"
                conversation_ids[run["id"]] = matching[0]["id"]
                save("state", state)
            conversation = api.get(f"/conversations/{conversation_ids[run['id']]}")
            settings = conversation.get("settings") or {}
            assert settings.get("writing_session_id") == session_id and settings.get("writing_project_id") == pid and settings.get("kind") == "writing_generation", "Conversation does not belong to this report-generation run"
            assistant = next((m for m in conversation.get("messages", []) if m.get("id") == run["assistant_message_id"]), None)
            assert assistant and assistant.get("role") == "assistant", "Persisted generation output is missing"
            if assistant.get("status") == "completed":
                break
            if assistant.get("status") in {"failed", "cancelled"} or run["status"] in {"quality_failed", "agent_failed", "cancelled"}:
                raise RuntimeError("Generation failed or was cancelled; protected history is retained. Rerun generate for a new attempt.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Generation is still running; no duplicate was started. Rerun generate to resume checking it.")
            if time.monotonic() - last_notice >= 30:
                emit("generation_waiting", {"id": run["id"], "status": run["status"], "progress": run.get("progress")})
                last_notice = time.monotonic()
            time.sleep(3)
        assert str(assistant.get("content") or "").strip(), "Completed Agent turn has no content"
        turn_events = [e for e in conversation.get("events", []) if e.get("message_id") == run["assistant_message_id"]]
        event_counts = Counter(e.get("event_type") for e in turn_events)
        assert event_counts["turn_completed"] and not event_counts["turn_failed"] and not event_counts["turn_cancelled"], "Persisted Agent turn did not complete successfully"
        assert any(e.get("event_type") == "tool_finished" and (e.get("payload") or {}).get("success") is not False for e in turn_events), "No successful tool execution was persisted"
        # Retain event names/counts only, not the private session or tool payloads.
        summary = {"run_id": run["id"], "counts": dict(event_counts), "streamed_event_count": len(streamed_events), "elapsed_seconds": round(time.monotonic()-started, 2)}
        save("generation_events", summary)
        if browser_owned_run:
            # The browser owns finalize for a turn it started. Do not race it
            # into duplicate report versions; observe its persisted outcome.
            deadline = time.monotonic() + 60
            while run["status"] == "agent_running" and time.monotonic() < deadline:
                time.sleep(1)
                run = api.get(f"/writing/generation-runs/{run['id']}")
            assert run["status"] == "completed", "Browser report finalization did not complete; its history is preserved"
        finalized = run if run["status"] == "completed" else api.post(f"/writing/generation-runs/{run['id']}/finalize", {})
        assert finalized["status"] == "completed" and (finalized.get("quality_report") or {}).get("ok") is True, "Formal report did not pass its production quality gate"
        assert finalized["project_id"] == pid and finalized["document"]["project_id"] == pid
        state["generation"] = finalized
        state["finalized"] = finalized
        from datetime import datetime
        if finalized.get("finished_at") and finalized.get("started_at"):
            summary["server_elapsed_seconds"] = round((datetime.fromisoformat(finalized["finished_at"]) - datetime.fromisoformat(finalized["started_at"])).total_seconds(), 2)
            save("generation_events", summary)
        save("state", state)
        emit("generated", {"document_id": state["finalized"]["document"]["id"], **summary, "quality": state["finalized"].get("quality_report")})
    elif args.stage in {"inspect", "export", "change-preview"}:
        pid = state["project"]["id"]
        did = state["finalized"]["document"]["id"]
        detail = api.get(f"/writing/documents/{did}")
        save("report_document", detail)
        save("paragraph_evidence", api.get(f"/writing/documents/{did}/paragraph-evidence"))
        if args.stage == "inspect":
            conversation_id = state.get("generation_conversation_ids", {}).get(state["finalized"]["id"])
            if conversation_id:
                conversation = api.get(f"/conversations/{conversation_id}")
                assert (conversation.get("settings") or {}).get("writing_project_id") == pid
                warnings = [{key: (event.get("payload") or {}).get(key) for key in ("code", "message", "warning")} for event in conversation.get("events", []) if event.get("event_type") == "warning"]
                save("generation_warnings", warnings)
                emit("generation_warnings", warnings)
            emit("report_structure", {"keys": list(detail), "document_id": did})
        elif args.stage == "export":
            import hashlib
            from io import BytesIO
            from zipfile import ZipFile

            validation = api.post(f"/writing/documents/{did}/validate", {"for_publish": False})
            save("report_validation", validation)
            assert validation.get("ok") is True and not validation.get("issues"), "Report failed source/freshness validation"
            assert (validation.get("quality_report") or {}).get("ok") is True, "Report is missing a successful production-quality review"
            current_version = detail["current_version"]
            assert current_version["id"] == detail["current_version_id"]
            require_confirmed_export_gates(api.get(f"/writing/projects/{pid}/decision-gates"))
            publish_validation = api.post(f"/writing/documents/{did}/validate", {"for_publish": True})
            save("export_validation", publish_validation)
            assert publish_validation.get("ok") is True and not publish_validation.get("pending_decision_gates"), "Report is not ready for export"
            exports = []
            for fmt in ["docx", "pdf", "evidence_docx"]:
                job = api.post(f"/writing/documents/{did}/exports", {"output_format": fmt})
                assert job["status"] == "succeeded", job["status"]
                assert job["document_id"] == did and job["document_version_id"] == current_version["id"] and job["output_format"] == fmt, "Export does not match the validated document version and format"
                response = api.client.get(f"/writing/exports/{job['id']}/download")
                api._raise(response)
                data = response.content
                checksum = hashlib.sha256(data).hexdigest()
                assert len(data) > 100 and checksum == job.get("checksum"), "Export bytes differ from the server artifact checksum"
                if fmt == "pdf":
                    assert data.startswith(b"%PDF-"), "Downloaded PDF has an invalid signature"
                else:
                    assert data.startswith(b"PK"), "Downloaded Word file is not a ZIP package"
                    with ZipFile(BytesIO(data)) as package:
                        assert package.testzip() is None, "Word ZIP package is corrupt"
                        assert {"[Content_Types].xml", "word/document.xml"}.issubset(package.namelist()), "Downloaded ZIP is not a Word document"
                ext = "docx" if fmt == "evidence_docx" else fmt
                name = "推演与来源依据" if fmt == "evidence_docx" else "青岚县安置保障与搜救协调报告"
                path = OUT / f"{name}.{ext}"
                path.write_bytes(data)
                exports.append({"format": fmt, "id": job["id"], "document_version_id": job["document_version_id"], "bytes": len(data), "sha256": checksum, "path": path.name})
                save("exports", exports)
            save("exports", exports)
            emit("exports", exports)
        else:
            before = api.get(f"/writing/documents/{did}/versions")
            facts_before = api.get(f"/writing/projects/{pid}/facts")
            expected_inputs = {"rescue_required": 500, "rescue_available": 320, "rescue_gap": 180}
            by_key = {fact["fact_key"]: fact for fact in facts_before}
            for key, value in expected_inputs.items():
                assert key in by_key and by_key[key]["value"].get("number") == value and by_key[key]["verification_status"] == "verified", f"Impact preview baseline is not the accepted 09:00 state: {key}"
            preview = api.post(f"/writing/projects/{pid}/input-changes/preview", {"document_id": did, "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "第二轮演练更新：11:00新增到位80人，按更新记录预览，不直接改正文"}]})
            save("change_preview", preview)
            assert preview["project_id"] == pid and preview["document_id"] == did and preview["status"] == "preview", "Unexpected input-change preview identity or status"
            impact = preview["impact"]
            assert impact["document_version_id"] == detail["current_version_id"] and impact.get("automatic_overwrite") is False, "Preview may not overwrite the current report"
            changes = impact["input_changes"]
            assert len(changes) == 1 and changes[0]["fact_key"] == "rescue_available" and changes[0]["old_value"] == {"number": 320} and changes[0]["new_value"] == {"number": 400}, "Preview input delta is incorrect"
            calculations = impact["calculations"]
            assert len(calculations) == 1 and calculations[0]["result_key"] == "rescue_gap" and calculations[0]["old_value"] == 180 and calculations[0]["new_value"] == 100, "Preview must calculate the rescue gap as 180 to 100"
            assert set(calculations[0]["dependencies"].values()) == {"rescue_required", "rescue_available"}, "Preview computation lost its input dependencies"
            assert impact["report_blocks"] and all(b.get("block_id") for b in impact["report_blocks"]), "Preview did not identify affected report blocks"
            after = api.get(f"/writing/documents/{did}/versions")
            assert before == after, "Preview must not alter report versions"
            assert facts_before == api.get(f"/writing/projects/{pid}/facts"), "Preview must not alter confirmed facts or calculated results"
            assert api.get(f"/writing/documents/{did}")["current_version_id"] == detail["current_version_id"], "Preview changed the current report version"
            emit("change_preview", preview)


if __name__ == "__main__":
    main()
