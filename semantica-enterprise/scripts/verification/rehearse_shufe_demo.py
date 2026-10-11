"""Real API rehearsal for the named SHUFE demo scope; credentials stay in memory.

No report prose or model answers are supplied by this script. Inspection is
read-only. Additional rehearsal steps are explicit and recorded separately.
"""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import time
import uuid
import sys
from collections import Counter
from copy import deepcopy

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SPACE_CODE = "shufe-feasibility-assets-20261010"
SOURCE_VERSION = "af1ee0e8-561f-46f9-a316-8d95d6392fa4"
PROJECT_NAME = "上财可研投资复核演示"
ARTICLE_TITLE = "科研楼投资估算与资金安排复核讨论稿"
FINAL_ARTICLE_TITLE = ARTICLE_TITLE + "（联动就绪版）"
ASSET_IDS = ["f113e2cf-5586-47a0-b308-ebea72c577bf", "a15f1ef7-fcd3-4b6d-8ffa-2996386f7ed4", "09d90ff4-4e98-41bd-99ae-e98531118748"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://10.5.113.232:9002")
    parser.add_argument("--step", choices=["inspect", "prepare", "update-input", "fresh-article", "refresh-ready-sources", "search", "generate", "verify", "verify-applied", "export"], default="inspect")
    parser.add_argument("--output", type=Path, default=Path(".demo-build/shufe-demo"))
    args = parser.parse_args()
    if args.url.rstrip("/") != "http://10.5.113.232:9002":
        raise ValueError("Only the user-selected test environment is allowed")
    args.output.mkdir(parents=True, exist_ok=True)
    password = getpass.getpass("Platform password (not saved): ")
    with httpx.Client(base_url=args.url.rstrip("/") + "/api/v1/", timeout=600) as client:
        login = client.post("auth/login", json={"username": "admin", "password": password})
        password = None
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                detail = response.json().get("detail") if "json" in response.headers.get("content-type", "") else "service error"
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}; {str(detail)[:500]}")
            return response.json()

        spaces = request("GET", "spaces")
        space = next(row for row in spaces if row["code"] == SPACE_CODE)
        receipt_path = args.output / "receipt.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {"space_id": space["id"]}
        def save():
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        def print_result(value):
            print(json.dumps(value, ensure_ascii=False), flush=True)
        if args.step != "inspect":
            if args.step == "prepare":
                if not receipt.get("release_id"):
                    reviewed = []
                    for original_id in ASSET_IDS:
                        trace = request("GET", f"writing-assets/{original_id}/trace")
                        assert trace["process"]["document_version_id"] == SOURCE_VERSION
                        asset = trace["asset"]
                        definition = deepcopy(asset["definition"])
                        # Explicit human mapping to the adopted chapter titles.
                        # The extracted formula and its project scope stay intact.
                        if asset["kind"] == "computation_method":
                            definition["used_in_sections"] = ["投资估算说明", "项目投资估算表"]
                        else:
                            definition["payload"]["chapters"][1]["instruction"] = "列示本次确认的建筑安装工程费用、工程建设其他费、基本预备费、原建筑拆除费用及项目总投资，单位万元；未提供的数量、单价不编造。"
                            definition["payload"]["chapters"][2]["instruction"] = "本次未提供新的资金批复或筹资计划，只说明待核实事项。不得照搬历史国拨、自筹金额或资金有保障结论。"
                        revised = request("POST", f"writing-assets/{asset['id']}/revise", json={"checksum": asset["checksum"], "definition": definition})
                        body = {"checksum": revised["checksum"], "action": "accept", "reason": "核对历史报告投资章节；明确映射使用章节，本次仅复核原项目方法，不批准新的投资或工程标准。"}
                        if asset["kind"] == "computation_method":
                            inputs = {field["key"]: 0 for field in definition["payload"]["inputs"]}
                            typical = {"construction_installation_cost": 10000, "other_engineering_cost": 1000, "basic_reserve_fee": 550, "demolition_cost": 200}
                            body.update(unit_confirmed=True, tests=[{"inputs": {key: typical[key] for key in inputs}, "expected": "550" if definition["key"] == "basic_reserve_fee" else "11750"}, {"inputs": inputs, "expected": "0"}])
                        reviewed.append(request("POST", f"writing-assets/{revised['id']}/review", json=body))
                    release = request("POST", "writing-assets/releases", json={"space_id": space["id"], "asset_ids": [row["id"] for row in reviewed]})
                    receipt.update(release_id=release["id"], asset_ids=[row["id"] for row in reviewed]); save()
                if not receipt.get("project_id"):
                    project = next((row for row in request("GET", "writing/projects") if row["name"] == PROJECT_NAME and row.get("knowledge_space_id") == space["id"]), None)
                    if project is None:
                        project = request("POST", "writing/projects", json={"name": PROJECT_NAME, "space_id": space["id"]})
                    receipt["project_id"] = project["id"]; save()
                pp = f"writing/projects/{receipt['project_id']}"
                if not receipt.get("input_document_id"):
                    path = Path("demo/shufe/投资复核本次输入.md")
                    with path.open("rb") as file:
                        uploaded = request("POST", "documents/upload", data={"space_id": space["id"], "knowledge_processing_targets": '["fulltext"]', "knowledge_processing_mode": "vector", "material_role": "task_data"}, files={"file": (path.name, file, "text/markdown")})
                    receipt.update(input_document_id=uploaded["document"]["id"], input_version_id=uploaded["version"]["id"], parse_job_id=uploaded["job"]["id"]); save()
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    detail = request("GET", f"documents/{receipt['input_document_id']}")
                    version = next(row for row in detail["versions"] if row["id"] == receipt["input_version_id"])
                    if version["status"] == "ready" and "fulltext" in (version.get("parse_summary") or {}).get("knowledge_targets_completed", []): break
                    if version["status"] == "failed": raise ValueError("本次材料真实加工失败")
                    time.sleep(3)
                else: raise ValueError("本次材料尚未发布，不能开始写作")
                attached = request("GET", pp + "/materials")
                for doc_id, version_id, role in [("d0579869-6396-4c78-9725-07b2bd0ca8e0", SOURCE_VERSION, "sample_style"), (receipt["input_document_id"], receipt["input_version_id"], "task_data")]:
                    if not any(row["version_id"] == version_id for row in attached):
                        request("POST", pp + "/materials", json={"document_id": doc_id, "version_id": version_id, "material_role": role})
                request("POST", pp + "/knowledge-release/refresh")
                if not receipt.get("article_id"):
                    article = request("POST", "writing/documents", json={"project_id": receipt["project_id"], "title": ARTICLE_TITLE, "document_type": "custom", "audience": "项目管理与财务复核人员", "purpose": "说明估算口径、费用组成及敏感性影响，形成讨论稿，不作立项审批结论", "applicability": {"time_range": "2026年10月", "matter": "原科研楼项目投资方法复核"}, "writing_requirements": "以本次复核输入和已确认计算生成三章正式讨论稿。历史可研仅参考结构与原项目方法，不迁移旧金额、时间、审批与职责。资金未落实只能说明待核实，不虚构价格、文号或制度。"})
                    receipt["article_id"] = article["id"]; save()
                ap = f"writing/documents/{receipt['article_id']}"
                article = request("GET", ap)
                if not article.get("applicability", {}).get("asset_bundle"):
                    request("POST", f"writing-assets/documents/{article['id']}/adopt", json={"release_id": receipt["release_id"], "asset_ids": receipt["asset_ids"], "base_version_id": article["current_version_id"], "scope_confirmed": True, "adopt_outline": True})
                facts = {row["fact_key"]: row for row in request("GET", pp + "/facts")}
                for key, label, value in [("construction_installation_cost", "建筑安装工程费用", 10000), ("other_engineering_cost", "工程建设其他费", 1000), ("demolition_cost", "原建筑拆除费用", 200)]:
                    if key in facts and facts[key]["verification_status"] == "verified":
                        if facts[key]["value"] != {"number": value}: raise ValueError("保留已修改的输入，不自动重置")
                        continue
                    fact = request("POST", pp + "/facts", json={"fact_key": key, "label": label, "value": {"number": value}, "unit": "万元", "source_type": "manual_input", "fact_type": "manual_input", "source_locator": {"document_version_id": receipt["input_version_id"], "constructed_input": True}})
                    request("POST", pp + f"/facts/{fact['id']}/confirm", json={"decision": "confirm", "reason": "核对本次复核输入材料，金额为人工敏感性假设，不是实际投资数据"})
                article = request("GET", ap)
                bundle = article["applicability"]["asset_bundle"]
                computed = request("POST", f"writing-assets/documents/{article['id']}/compute", json={"base_version_id": article["current_version_id"], "release_id": bundle["release_id"], "checksum": bundle["checksum"]})
                values = {item["fact"]["fact_key"]: item["fact"]["value"]["number"] for item in computed["items"]}
                assert values["basic_reserve_fee"] == 550 and values["total_investment"] == 11750
                receipt["initial_calculations"] = values; save(); print_result(receipt); return
            pp = f"writing/projects/{receipt['project_id']}"
            ap = f"writing/documents/{receipt['article_id']}"
            if args.step == "update-input":
                path = Path("demo/shufe/投资复核本次输入.md")
                with path.open("rb") as file:
                    uploaded = request("POST", "documents/upload", data={"space_id": space["id"], "knowledge_processing_targets": '["fulltext"]', "knowledge_processing_mode": "vector", "material_role": "task_data"}, files={"file": (path.name, file, "text/markdown")})
                receipt.update(input_document_id=uploaded["document"]["id"], input_version_id=uploaded["version"]["id"], parse_job_id=uploaded["job"]["id"]); save()
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    detail = request("GET", f"documents/{receipt['input_document_id']}")
                    version = next(row for row in detail["versions"] if row["id"] == receipt["input_version_id"])
                    if version["status"] == "ready" and "fulltext" in (version.get("parse_summary") or {}).get("knowledge_targets_completed", []): break
                    if version["status"] == "failed": raise ValueError("本次材料真实加工失败")
                    time.sleep(3)
                else: raise ValueError("本次材料索引尚未完成")
                request("POST", pp + "/materials", json={"document_id": receipt["input_document_id"], "version_id": receipt["input_version_id"], "material_role": "task_data"})
                request("POST", pp + "/knowledge-release/refresh")
                for key, label, value in [("construction_installation_cost", "建筑安装工程费用", 10000), ("other_engineering_cost", "工程建设其他费", 1000), ("demolition_cost", "原建筑拆除费用", 200)]:
                    fact = request("POST", pp + "/facts", json={"fact_key": key, "label": label, "value": {"number": value}, "unit": "万元", "source_type": "manual_input", "fact_type": "manual_input", "source_locator": {"document_version_id": receipt["input_version_id"], "constructed_input": True}})
                    request("POST", pp + f"/facts/{fact['id']}/confirm", json={"decision": "confirm", "reason": "核对分离了操作说明的新输入材料；金额假设不变，不是实际投资数据"})
                print_result({"input_ready": True, "version_id": receipt["input_version_id"]}); return
            if args.step == "refresh-ready-sources":
                detail = request("GET", f"documents/{receipt['input_document_id']}")
                version = next(row for row in detail["versions"] if row["id"] == receipt["input_version_id"])
                assert version["status"] == "ready" and "fulltext" in (version.get("parse_summary") or {}).get("knowledge_targets_completed", []), "真实索引尚未发布，不能锁定写作快照"
                refreshed = request("POST", pp + "/knowledge-release/refresh")
                receipt.setdefault("prior_generation_runs", []).append({"run_id": receipt.get("run_id"), "generation": receipt.get("generation")})
                receipt.pop("run_id", None); receipt.pop("generation", None)
                receipt["source_refresh"] = refreshed; save()
                print_result({"sources_ready": True}); return
            if args.step == "search":
                result = request("POST", pp + "/knowledge/search", json={"document_id": receipt["article_id"], "query": "基本预备费", "use_keyword": True, "use_vector": False, "use_graph": False, "use_reranker": False})
                receipt["search_diagnostic"] = result; save()
                article = request("GET", ap)
                context = request("GET", pp + "/knowledge-context", params={"document_id": article["id"]})
                (args.output / "search-context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
                print_result({"count": len(result.get("items", [])), "warnings": result.get("warnings"), "release_id": result.get("knowledge_product_release_id"), "article_release": article.get("knowledge_product_release_id"), "adopted_material_ids": article.get("adopted_material_ids"), "context": context}); return
            if args.step == "fresh-article":
                old = request("GET", ap)
                article = next((row for row in request("GET", pp + "/documents")
                                if row["title"] == FINAL_ARTICLE_TITLE), None)
                if article is None:
                    article = request("POST", "writing/documents", json={
                    "project_id": receipt["project_id"], "title": FINAL_ARTICLE_TITLE,
                    "document_type": old["document_type"], "audience": old["audience"],
                    "purpose": old["purpose"], "writing_requirements": old["writing_requirements"] + "正文不写软件操作、输入登记状态或候选变更过程。只呈现已确认费用口径、金额组成、计算结果和资金待核实事项；不要描述修改指标或同步正文的操作。",
                    "applicability": {"time_range": "2026年10月", "matter": "原科研楼项目投资方法复核"}})
                materials = request("GET", pp + "/materials")
                selected = [row["id"] for row in materials if row["version_id"] in {SOURCE_VERSION, receipt["input_version_id"]}]
                assert len(selected) == 2
                request("PUT", f"writing/documents/{article['id']}/materials", json={"material_ids": selected})
                request("POST", f"writing-assets/documents/{article['id']}/adopt", json={
                    "release_id": receipt["release_id"], "asset_ids": receipt["asset_ids"],
                    "base_version_id": article["current_version_id"], "scope_confirmed": True,
                    "adopt_outline": not bool(article.get("applicability", {}).get("native_outline"))})
                article = request("GET", f"writing/documents/{article['id']}")
                bundle = article["applicability"]["asset_bundle"]
                request("POST", f"writing-assets/documents/{article['id']}/compute", json={
                    "base_version_id": article["current_version_id"], "release_id": bundle["release_id"], "checksum": bundle["checksum"]})
                receipt.setdefault("prior_rehearsals", []).append({"article_id": receipt["article_id"], "run_id": receipt.get("run_id"), "verification": receipt.get("verification")})
                receipt["article_id"] = article["id"]
                for key in ("run_id", "generation", "verification", "exports"):
                    receipt.pop(key, None)
                save(); print_result({"new_article_id": article["id"], "material_count": len(selected)}); return
            if args.step == "generate":
                preflight_search = request("POST", pp + "/knowledge/search", json={"document_id": receipt["article_id"], "query": "基本预备费", "use_keyword": True, "use_vector": False, "use_graph": False, "use_reranker": False})
                assert any(item.get("version_id") == receipt["input_version_id"] for item in preflight_search.get("items", [])), "当前文章快照不能检索本次输入版本，禁止开始生成"
                run = request("GET", f"writing/generation-runs/{receipt['run_id']}") if receipt.get("run_id") else request("POST", pp + "/generate-report", json={"document_id": receipt["article_id"], "allow_partial": False})
                receipt["run_id"] = run["id"]; save()
                events = Counter((receipt.get("generation") or {}).get("events") or {})
                for turn in range(8):
                    if run["status"] == "completed": break
                    print_result({"turn": turn + 1, "status": run["status"]})
                    with client.stream("POST", f"writing/generation-runs/{run['id']}/agent") as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if line.startswith("event: "): events[line[7:]] += 1
                    try:
                        run = request("POST", f"writing/generation-runs/{run['id']}/finalize")
                    except RuntimeError:
                        failed = request("GET", f"writing/generation-runs/{run['id']}")
                        attempts = receipt.setdefault("failed_attempts", [])
                        run_attempts = [item for item in attempts if item["run_id"] == run["id"]]
                        if failed.get("error_code") not in {"INVALID_AGENT_REPORT", "INVALID_AGENT_DEPENDENCIES"} or len(run_attempts) >= 2:
                            raise
                        attempts.append({"run_id": run["id"], "error_code": failed["error_code"], "message": failed.get("error_message")})
                        receipt["generation"] = {"status": failed["status"], "stage": failed["stage"], "events": dict(events), "quality": failed.get("quality_report")}; save()
                        print_result({"retrying_rejected_chapter": True, "error_code": failed["error_code"]})
                        run = failed
                        continue
                    receipt["generation"] = {"status": run["status"], "stage": run["stage"], "events": dict(events), "quality": run.get("quality_report")}; save()
                    print_result(receipt["generation"])
                    if run["status"] not in {"awaiting_agent", "completed"}: raise ValueError("真实生成未通过质量检查，保留失败证据")
                assert run["status"] == "completed"; return
            article = request("GET", ap)
            if args.step == "verify-applied":
                before = json.loads((args.output / "article-before.json").read_text(encoding="utf-8"))
                assert article["current_version_id"] != before["current_version_id"], "未产生应用后的新版本"
                facts = {row["fact_key"]: row for row in request("GET", pp + "/facts")}
                expected = {"construction_installation_cost": 11000, "other_engineering_cost": 1000,
                            "demolition_cost": 200, "basic_reserve_fee": 600, "total_investment": 12800}
                for key, value in expected.items():
                    assert facts[key]["value"]["number"] == value, f"{key} 权威输入或计算结果错误"
                from packages.platform.writing_occurrences import validate_numeric_occurrences, numeric_value
                occurrences = [item for node in article["current_version"]["content"] for item in validate_numeric_occurrences(node)]
                for item in occurrences:
                    if item.get("fact_key") in expected:
                        assert numeric_value(item["value"]) == numeric_value(expected[item["fact_key"]]), "已绑定正文数字未一致更新"
                versions = request("GET", ap + "/versions")
                previous = next(row for row in versions if row["id"] == before["current_version_id"])
                assert previous["content"] == before["current_version"]["content"], "历史正文被覆盖"
                for old_article_id in ("6cdabdb2-cdf4-456d-9de1-a4a78e229b7b", "aa72dc02-259a-44d7-b95d-b58b939730d3"):
                    old_article = request("GET", f"writing/documents/{old_article_id}")
                    old_numbers = [item for node in old_article["current_version"]["content"] for item in validate_numeric_occurrences(node)]
                    assert not any(item.get("fact_key") == "construction_installation_cost" and numeric_value(item["value"]) == 11000 for item in old_numbers), "其他文章被静默改写"
                quality = request("POST", ap + "/validate", json={})
                assert quality.get("ok"), "应用后的正文审校未通过"
                (args.output / "article-after.json").write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["applied_verification"] = {"quality": quality, "values": expected, "numeric_occurrences": len(occurrences), "historical_version_unchanged": True, "other_articles_not_rewritten": True, "article_version": article["current_version"]["version"]}
                save(); print_result(receipt["applied_verification"]); return
            if args.step == "verify":
                search = request("POST", pp + "/knowledge/search", json={"document_id": article["id"], "query": "基本预备费", "use_keyword": True, "use_vector": False, "use_graph": False, "use_reranker": False})
                assert search.get("items"), "没有真实检索结果，不能将这一步记录为通过"
                bindings = request("GET", ap + "/bindings")
                (args.output / "bindings.json").write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
                quality = request("POST", ap + "/validate", json={})
                assert quality.get("ok"), "真实正文审校没有通过"
                def node_text(node):
                    return str(node.get("text") or "") + "".join(node_text(child) for child in node.get("children") or [])
                text = "\n".join(node_text(node) for node in article["current_version"]["content"])
                assert not any(word in text for word in ("影响预览", "同步更新绑定", "11000", "登记了一项待确认变更")), "操作说明或未确认候选变化进入了业务正文"
                from packages.platform.writing_occurrences import validate_numeric_occurrences, numeric_value
                ordinary = [node for node in article["current_version"]["content"] if node.get("type") != "computed_metric"]
                occurrences = [occurrence for node in ordinary for occurrence in validate_numeric_occurrences(node)]
                for key in ("basic_reserve_fee", "total_investment"):
                    assert any(row.get("fact_key") == key for row in occurrences), f"{key} 没有正文/表格数字位置，不能称为联动成功"
                tables = [node for node in ordinary if node.get("type") == "table"]
                assert tables, "投资报告没有实际表格"
                table_occurrences = [item for table in tables for item in validate_numeric_occurrences(table)]
                for key in ("construction_installation_cost", "basic_reserve_fee", "total_investment"):
                    assert any(item.get("fact_key") == key for item in table_occurrences), f"{key} 未登记表格金额位置"
                preview = request("POST", pp + "/input-changes/preview", json={"document_id": article["id"], "changes": [{"fact_key": "construction_installation_cost", "new_value": {"number": 11000}, "reason": "本次敏感性复核：建安费增加1000万元，其他输入不变"}]})
                unchanged = request("GET", ap)
                assert unchanged["current_version_id"] == article["current_version_id"]
                calculated = {row["result_key"]: row["new_value"] for row in preview["impact"]["calculations"]}
                assert calculated["basic_reserve_fee"] == 600 and calculated["total_investment"] == 12800
                for proposal in preview["impact"]["content_proposals"]:
                    if not proposal.get("selectable") or not proposal.get("new_node"):
                        continue
                    for occurrence in validate_numeric_occurrences(proposal["new_node"]):
                        if occurrence.get("fact_key") in calculated:
                            assert numeric_value(occurrence["value"]) == numeric_value(calculated[occurrence["fact_key"]])
                (args.output / "preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
                (args.output / "article-before.json").write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["verification"] = {"search": search, "binding_count": len(bindings), "quality": quality, "preview_id": preview["id"], "preview_did_not_change_article": True}; save()
                print_result({"binding_count": len(bindings), "quality": quality, "calculations": preview["impact"]["calculations"], "proposal_count": len(preview["impact"]["content_proposals"]), "search_keys": list(search)}); return
            if args.step == "export":
                import io
                import zipfile
                support = client.get(pp + "/writing-support.zip", params={"document_id": article["id"]})
                support.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(support.content)) as archive:
                    names = archive.namelist()
                    assert any(name.endswith(".yaml") for name in names) and any(name.endswith(".md") for name in names)
                    assert not any(b"runtime_prompt:" in archive.read(name) for name in names)
                (args.output / "写作支撑数据.zip").write_bytes(support.content)
                receipt["support_export"] = {"files": names, "bytes": len(support.content)}; save()
                for fmt in ("docx", "pdf"):
                    job = request("POST", ap + "/exports", json={"output_format": fmt})
                    receipt.setdefault("exports", {})[fmt] = job; save()
                    response = client.get(f"writing/exports/{job['id']}/download"); response.raise_for_status()
                    target = args.output / f"投资复核讨论稿.{fmt}"
                    target.write_bytes(response.content)
                    print_result({"format": fmt, "bytes": len(response.content), "status": job["status"]})
                return
        summary = request("GET", "writing-assets/summary", params={"space_id": space["id"]})
        selected = []
        for kind in ("report_structure", "report_form", "computation_method"):
            offset = 0
            while True:
                rows = request("GET", "writing-assets", params={"space_id": space["id"], "kind": kind, "limit": 500, "offset": offset})
                for row in rows:
                    # Inspect writing/cost candidates, not every engineering
                    # clause. Source identity is still verified through trace.
                    context = row["title"] + row["definition"].get("purpose", "")
                    if not any(word in context for word in ("可研", "可行性", "投资", "估算", "费用", "科研", "研究中心", "资金", "造价")):
                        continue
                    trace = request("GET", f"writing-assets/{row['id']}/trace")
                    if trace["process"]["document_version_id"] == SOURCE_VERSION:
                        selected.append(row)
                if len(rows) < 500:
                    break
                offset += len(rows)
        snapshot = {"checked_at_epoch": int(time.time()), "space": space, "summary": summary,
                    "customer_report_assets": selected}
        (args.output / "inspection.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"space": space["name"], "asset_versions": summary["total"],
                          "report_asset_count": len(selected)}, ensure_ascii=False))
        for row in selected:
            print(json.dumps({"id": row["id"], "title": row["title"], "kind": row["kind"],
                              "key": row["definition"]["key"], "version": row["version"],
                              "payload": row["definition"]["payload"] if row["kind"] == "computation_method" else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
