"""Opt-in, repeatable live check: sample structure + new factual article.

Only the named acceptance space/project is touched.  The source PDF is a
style/outline sample, never a factual source for the generated article.
Authentication comes from the local secure environment, not this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from scripts.miaobi.acceptance_upload_to_report import parse_sse
from scripts.miaobi.demo_client import DemoClient


CODE = "miaobi-sample-profile-acceptance-20260915"
SOURCE = Path("/Users/tianqi/Desktop/积石山县6.2级地震_本体驱动应急智能推演系统_完整升级版")
SAMPLE = SOURCE / "样稿.pdf"
FACTUAL = SOURCE / "临夏州地震应急预案.docx"


def _emit(stage: str, value: dict) -> None:
    print(json.dumps({"stage": stage, "result": value}, ensure_ascii=False), flush=True)


def _same_version(api: DemoClient, space_id: str, path: Path) -> dict | None:
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    for document in api.get("/documents", space_id=space_id):
        if document.get("title") != path.name:
            continue
        detail = api.get(f"/documents/{document['id']}")
        version = next((row for row in detail.get("versions", []) if row.get("sha256") == expected), None)
        if version and version.get("status") in {"processed", "published", "ready"}:
            return {"document": document, "version": version}
    return None


def _material(api: DemoClient, project_id: str, upload: dict, role: str) -> dict:
    document_id = upload["document"]["id"]
    version_id = upload["version"]["id"]
    for row in api.get(f"/writing/projects/{project_id}/materials"):
        if row.get("document_id") == document_id and row.get("version_id") == version_id:
            return row
    return api.post(
        f"/writing/projects/{project_id}/materials",
        {"document_id": document_id, "version_id": version_id, "material_role": role},
    )


def setup(api: DemoClient) -> dict:
    for path in (SAMPLE, FACTUAL):
        if not path.is_file():
            raise FileNotFoundError(path)
    space = api.one("/spaces", "code", CODE)
    if space is None:
        space = api.post("/spaces", {
            "code": CODE, "name": "妙笔·样稿结构与新文章验收空间",
            "description": "演练/测试数据；用于真实来源解析、样稿配置提炼与独立新文章质量验证。",
            "enabled": True,
        })
    uploaded = {}
    for label, path in (("sample", SAMPLE), ("factual", FACTUAL)):
        match = _same_version(api, space["id"], path)
        uploaded[label] = match if match else api.upload_file(space["id"], path, mode="vector")
        _emit("upload", {
            "role": label, "name": path.name,
            "document_id": uploaded[label]["document"]["id"],
            "version_id": uploaded[label]["version"]["id"],
            "status": uploaded[label]["version"].get("status"),
        })
    project = api.one("/writing/projects", "code", CODE)
    if project is None:
        project = api.post("/writing/projects", {
            "code": CODE, "name": "临夏州地震应急预案讨论稿·样稿结构验证",
            "space_id": space["id"],
            "config": {"disclaimer": "基于上传依据生成的测试讨论稿，未经业务主管部门审核，不代表正式预案或行政决定。"},
        })
    sample_material = _material(api, project["id"], uploaded["sample"], "sample_style")
    factual_material = _material(api, project["id"], uploaded["factual"], "policy_basis")
    documents = api.get(f"/writing/projects/{project['id']}/documents")
    article = next((row for row in documents if row.get("title") == "临夏州地震应急预案（项目讨论稿）"), None)
    if article is None:
        article = api.post("/writing/documents", {
            "project_id": project["id"], "title": "临夏州地震应急预案（项目讨论稿）",
            "document_type": "emergency_plan", "audience": "临夏州应急管理和相关部门业务讨论",
            "purpose": "依据当前上传预案整理一份可审阅、可追溯的地震应急预案讨论稿；不得冒充正式签发文件。",
            "applicability": {"region": "临夏州", "status": "讨论稿", "data_time": "上传依据版本"},
            "writing_requirements": "参照样稿的一级章节和正式文体组织全文；部门职责、阈值和响应权限只按临夏州依据表达，不迁移样稿中的汕尾市事实。每章写具体行动与衔接，避免空泛与重复。",
        })
    article_id = article["id"]
    current = api.get(f"/writing/documents/{article_id}")
    if (current.get("applicability") or {}).get("sample_profile", {}).get("status") != "confirmed":
        result = api.post(f"/writing/documents/{article_id}/sample-profile/preview", {"material_id": sample_material["id"]})
        profile = result["profile"]
        if profile.get("indicator_candidates") or profile.get("formula_candidates"):
            raise AssertionError("样稿结构提炼不应伪造指标或公式")
        api.put(f"/writing/documents/{article_id}/sample-profile", {
            "material_id": sample_material["id"], "profile": profile,
        })
    else:
        profile = current["applicability"]["sample_profile"]
    if len(profile["chapters"]) != 7:
        raise AssertionError(f"客户样稿一级章节应为 7 个，实际为 {len(profile['chapters'])}")
    _emit("profile", {
        "project_id": project["id"], "article_id": article_id,
        "space_id": space["id"], "sample_version_id": uploaded["sample"]["version"]["id"],
        "factual_version_id": uploaded["factual"]["version"]["id"],
        "factual_material_id": factual_material["id"],
        "chapters": [row["title"] for row in profile["chapters"]],
        "sample_not_factual": profile["style"]["sample_is_not_factual_evidence"],
    })
    return {"space": space, "project": project, "article": article, "profile": profile}


def generate(api: DemoClient, context: dict, existing_run_id: str | None = None) -> dict:
    project_id = context["project"]["id"]
    article_id = context["article"]["id"]
    current = api.get(f"/writing/documents/{article_id}")
    if existing_run_id:
        run = api.get(f"/writing/generation-runs/{existing_run_id}")
        if run["document_id"] != article_id:
            raise RuntimeError("恢复任务不属于本文")
    else:
        if current.get("current_version", {}).get("change_summary") not in {"创建文稿", "创建报告草稿"}:
            raise RuntimeError("本文已有生成或人工正文；本验收不会覆盖，只检查现存文章")
        run = api.post(f"/writing/projects/{project_id}/generate-report", {"document_id": article_id})
    _emit("generation_started", {"run_id": run["id"], "status": run["status"], "section_count": len(run["section_plan"])})
    if run["status"] == "quality_failed" and run.get("assistant_message_id"):
        run = api.post(f"/writing/generation-runs/{run['id']}/finalize", {})
        _emit("chapter_recovered", {"run_id": run["id"], "status": run["status"], "progress": run["progress"]})
    while run["status"] == "awaiting_agent":
        with api.client.stream("POST", f"/writing/generation-runs/{run['id']}/agent", headers={"Accept": "text/event-stream"}) as response:
            api._raise(response)
            events = parse_sse(response)
        counts = Counter(name for name, _ in events)
        _emit("agent_stream", {"run_id": run["id"], "events": dict(counts)})
        run = api.post(f"/writing/generation-runs/{run['id']}/finalize", {})
        _emit("chapter_checked", {"run_id": run["id"], "status": run["status"], "progress": run["progress"]})
    final = run
    _emit("finalize", {
        "run_id": run["id"], "status": final.get("status"),
        "quality": final.get("quality_report"),
    })
    return final


def inspect(api: DemoClient, context: dict) -> dict:
    article_id = context["article"]["id"]
    detail = api.get(f"/writing/documents/{article_id}")
    content = (detail.get("current_version") or {}).get("content") or []
    headings = []
    paragraphs = []
    for node in content:
        text = "".join(str(child.get("text") or "") for child in node.get("children") or [])
        if node.get("type") in {"h1", "h2", "h3"}:
            headings.append({"level": node["type"], "text": text})
        elif node.get("type") in {"p", "li"}:
            paragraphs.append(text)
    plain = "\n".join(row["text"] for row in headings) + "\n" + "\n".join(paragraphs)
    chapter_titles = [row["title"] for row in context["profile"]["chapters"]]
    full_reference_chars = context["profile"]["style"]["reference_characters"]
    full_ratio = len(re.sub(r"\s+", "", plain)) / max(1, full_reference_chars)
    runs = api.get(f"/writing/projects/{context['project']['id']}/generation-runs")
    current_run = next((row for row in runs if row.get("document_id") == article_id and row.get("status") == "completed"), None)
    body_ratio = ((current_run or {}).get("quality_report") or {}).get("metrics", {}).get("length_ratio")
    result = {
        "article_id": article_id, "version": (detail.get("current_version") or {}).get("version"),
        "headings": headings, "paragraph_count": len(paragraphs), "characters": len(plain),
        "sample_body_length_ratio": body_ratio,
        "sample_full_file_length_ratio": round(full_ratio, 3),
        "missing_chapters": [title for title in chapter_titles if not any(row["text"] == title for row in headings)],
        "citation_count": sum(1 for node in content for child in node.get("children") or [] if child.get("type") == "knowledge_citation"),
        "first_paragraphs": [text[:240] for text in paragraphs[:5]],
    }
    _emit("article_quality", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["setup", "generate", "resume", "revise-closing", "inspect"])
    stage = parser.parse_args().stage
    api = DemoClient()
    context = setup(api) if stage == "setup" else None
    if context is None:
        space = api.one("/spaces", "code", CODE)
        project = api.one("/writing/projects", "code", CODE)
        if not space or not project:
            raise RuntimeError("请先执行 setup")
        article = next((row for row in api.get(f"/writing/projects/{project['id']}/documents") if row["title"] == "临夏州地震应急预案（项目讨论稿）"), None)
        if not article:
            raise RuntimeError("请先执行 setup，创建新文章")
        profile = (api.get(f"/writing/documents/{article['id']}").get("applicability") or {}).get("sample_profile")
        if not profile or profile.get("status") != "confirmed":
            raise RuntimeError("样稿配置尚未确认")
        context = {"space": space, "project": project, "article": article, "profile": profile}
    if stage == "generate":
        generate(api, context)
    elif stage == "resume":
        latest = api.get(f"/writing/projects/{context['project']['id']}/generation-runs")
        if not latest or latest[0]["document_id"] != context["article"]["id"]:
            raise RuntimeError("当前文章没有可恢复的真实写作运行")
        generate(api, context, latest[0]["id"])
    elif stage == "revise-closing":
        latest = api.get(f"/writing/projects/{context['project']['id']}/generation-runs")
        if not latest or latest[0]["document_id"] != context["article"]["id"]:
            raise RuntimeError("本文没有可按章修订的真实写作任务")
        revision = api.post(f"/writing/generation-runs/{latest[0]['id']}/revise-section",
                            {"section_key": "sample-section-7"})
        _emit("closing_revision_started", {"run_id": revision["id"], "status": revision["status"]})
        generate(api, context, revision["id"])
    elif stage == "inspect":
        inspect(api, context)


if __name__ == "__main__":
    main()
