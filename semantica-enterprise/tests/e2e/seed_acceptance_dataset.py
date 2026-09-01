#!/usr/bin/env python3
"""Seed a deterministic, user-visible clean-slate acceptance dataset.

The script intentionally uses only public platform APIs.  It leaves the data
in place so the same spaces, documents, rules and inference result can be
inspected in the browser after the automated checks finish.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "generated"
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        login = self.call("POST", "/auth/login", json={"username": USERNAME, "password": PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1200]}")
        if not response.content:
            return {}
        return response.json()

    def wait_job(self, job_id: str, *, timeout: int = 1200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.call("GET", f"/jobs/{job_id}")
            if last["status"] in {"succeeded", "failed"}:
                if last["status"] != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:3000])
                return last
            time.sleep(1.5)
        raise TimeoutError(f"job {job_id} did not finish: {last}")

    def wait_knowledge_job(self, version_id: str, *, timeout: int = 1200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = self.call("GET", "/jobs")
            job = next(
                (
                    row
                    for row in jobs
                    if row.get("job_type") == "process_knowledge"
                    and (row.get("input") or {}).get("version_id") == version_id
                ),
                None,
            )
            if job:
                return self.wait_job(job["id"], timeout=max(1, int(deadline - time.monotonic())))
            time.sleep(1)
        raise TimeoutError(f"knowledge job for version {version_id} was not created")

    def upload_bytes(self, space_id: str, filename: str, body: bytes) -> dict[str, Any]:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        uploaded = self.call(
            "POST",
            "/documents/upload",
            data={"space_id": space_id},
            files={"file": (filename, body, content_type)},
        )
        parse_job = self.wait_job(uploaded["job"]["id"])
        knowledge_job = self.wait_knowledge_job(uploaded["version"]["id"])
        detail = self.call("GET", f"/documents/{uploaded['document']['id']}")
        elements = self.call("GET", f"/versions/{uploaded['version']['id']}/elements?limit=500")
        return {
            "document_id": uploaded["document"]["id"],
            "version_id": uploaded["version"]["id"],
            "filename": filename,
            "parse_job": parse_job["status"],
            "knowledge_job": knowledge_job["status"],
            "elements": len(elements.get("items") or elements),
            "profile": bool(detail.get("profile")),
            "quality_score": (detail.get("profile") or {}).get("quality_score"),
            "parse_summary": detail["versions"][0].get("parse_summary") or {},
        }


def ensure_clean(platform: Platform) -> None:
    existing = {row["code"] for row in platform.call("GET", "/spaces")}
    conflicts = existing & {"acceptance-main", "acceptance-analysis"}
    if conflicts:
        raise RuntimeError(f"验收空间已存在，请先清理后再运行：{sorted(conflicts)}")


def create_analysis_dataset(platform: Platform, space_id: str) -> dict[str, Any]:
    entities: dict[str, str] = {}
    for name, entity_type in (
        ("集团数据治理制度", "制度"),
        ("国联集团", "组织"),
        ("国联数字科技有限公司", "组织"),
        ("华东算力设备有限公司", "供应商"),
        ("算力一体机 X1", "产品"),
        ("关键产品", "产品分类"),
    ):
        row = platform.call(
            "POST",
            "/knowledge/entities",
            json={"space_id": space_id, "canonical_name": name, "entity_type": entity_type},
        )
        entities[name] = row["id"]

    facts = [
        ("集团数据治理制度", "适用于", "国联集团"),
        ("国联集团", "管理", "国联数字科技有限公司"),
        ("华东算力设备有限公司", "供应", "算力一体机 X1"),
        ("算力一体机 X1", "属于", "关键产品"),
    ]
    for subject, predicate, object_name in facts:
        platform.call(
            "POST",
            "/knowledge/facts",
            json={
                "space_id": space_id,
                "subject_entity_id": entities[subject],
                "predicate": predicate,
                "object_entity_id": entities[object_name],
                "confidence": 0.98,
            },
        )

    rule_set = platform.call(
        "POST",
        "/analysis/rule-sets",
        json={
            "name": "集团制度与供应链推理规则",
            "description": "验证制度适用范围传递与关键供应商识别",
            "category": "集团治理",
            "space_ids": [space_id],
        },
    )
    rules = [
        (
            "制度适用范围向下传递",
            [
                {"predicate": "适用于", "subject": "X", "object": "Y"},
                {"predicate": "管理", "subject": "Y", "object": "Z"},
            ],
            {"predicate": "适用于", "subject": "X", "object": "Z"},
        ),
        (
            "关键供应商识别",
            [
                {"predicate": "供应", "subject": "X", "object": "Y"},
                {"predicate": "属于", "subject": "Y", "object": "Z"},
            ],
            {"predicate": "属于", "subject": "X", "object": "Z"},
        ),
    ]
    rule_ids = []
    for name, conditions, conclusion in rules:
        definition = {"conditions": conditions, "conclusion": conclusion}
        validation = platform.call(
            "POST", "/analysis/rules/validate", json={"name": name, "definition": definition}
        )
        if not validation.get("valid"):
            raise RuntimeError(f"规则校验失败：{validation}")
        rule = platform.call(
            "POST",
            f"/analysis/rule-sets/{rule_set['id']}/rules",
            json={"name": name, "definition": definition, "confidence": 0.95},
        )
        rule_ids.append(rule["id"])

    scenario = platform.call(
        "POST",
        "/analysis/scenarios",
        json={
            "name": "集团制度与供应链分析",
            "description": "新增知识后可重复执行的集团治理场景",
            "category": "集团治理",
            "rule_set_id": rule_set["id"],
            "space_ids": [space_id],
            "config": {"max_results": 100},
        },
    )
    saved_query = platform.call(
        "POST",
        "/analysis/saved-queries",
        json={
            "name": "查看全部断言与推导关系",
            "query_type": "sparql",
            "space_ids": [space_id],
            "query_text": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 100",
        },
    )
    started = platform.call(
        "POST",
        "/analysis/inference-runs",
        json={
            "rule_set_id": rule_set["id"],
            "scenario_id": scenario["id"],
            "space_ids": [space_id],
            "mode": "publish",
        },
    )
    platform.wait_job(started["job_id"])
    run = platform.call("GET", f"/analysis/inference-runs/{started['id']}")
    if run["status"] != "succeeded" or len(run.get("items") or []) != 2:
        raise RuntimeError(f"预期 2 条推导事实，实际为：{json.dumps(run, ensure_ascii=False)[:3000]}")
    return {
        "entities": len(entities),
        "asserted_facts": len(facts),
        "rule_set_id": rule_set["id"],
        "rule_ids": rule_ids,
        "scenario_id": scenario["id"],
        "saved_query_id": saved_query["id"],
        "inference_run_id": run["id"],
        "inferred_facts": len(run["items"]),
        "engine": (run.get("metrics") or {}).get("engine"),
    }


def main() -> None:
    platform = Platform()
    ensure_clean(platform)
    main_space = platform.call(
        "POST",
        "/spaces",
        json={
            "code": "acceptance-main",
            "name": "集团产品知识验收库",
            "description": "用于验证多模态解析、自动治理、混合检索、图谱和智能问答",
        },
    )
    analysis_space = platform.call(
        "POST",
        "/spaces",
        json={
            "code": "acceptance-analysis",
            "name": "制度与供应链分析库",
            "description": "用于验证 Semantica Datalog 推理、证据链、发布和 SPARQL",
        },
    )

    handbook = """# 传神智库验收手册

## 产品定位
传神智库是面向集团员工、模型与智能体的组织级知识基础设施。它不是单纯的附件仓库，而是统一的知识治理、检索、分析和服务底座。

## 数据接入
平台支持 Web、REST API、RSS、Sitemap、Git、PostgreSQL、MySQL、S3/MinIO、SFTP、FTP、WebDAV、SMB、MCP、IMAP 和 POP3 等来源，并通过增量游标与内容哈希避免重复版本。

## 自动治理
文档接入后依次完成多模态解析、切片、实体与关系抽取、自动摘要、分类、标签、关键词和质量评分。模型治理失败时保留确定性质量结果并允许重试。

## 知识服务
系统通过全文、向量和图谱三路召回，使用 RRF 融合并支持可选重排。DeepSeek Harness 负责多轮 Agent Loop，回答展示真实引用、执行过程和检索轨迹。外部应用可以通过 REST、MCP 和 CLI 调用知识。

## 验收事实
产品于 2026 年 9 月进入组织级验收阶段。首期优先级依次为知识治理、混合检索、智能问答、知识分析和开放服务。
""".encode("utf-8")
    policy = """# 集团数据治理制度

集团数据治理制度适用于国联集团。国联集团管理国联数字科技有限公司。
制度要求知识来源可追溯、处理结果可回滚、模型密钥不可出现在浏览器和日志中。

华东算力设备有限公司供应算力一体机 X1。算力一体机 X1 属于关键产品。
当供应商提供关键产品时，应将其纳入关键供应商持续监测范围。
""".encode("utf-8")
    uploads: list[tuple[str, bytes]] = [
        ("传神智库验收手册.md", handbook),
        ("集团数据治理制度.md", policy),
    ]
    for filename in (
        "fact.pdf",
        "scanned.pdf",
        "fact.docx",
        "fact.pptx",
        "fact.xlsx",
        "fact.png",
        "fact.eml",
        "fact.mp3",
        "fact.mp4",
        "fact.zip",
    ):
        uploads.append((filename, (FIXTURES / filename).read_bytes()))

    documents = []
    for filename, body in uploads:
        result = platform.upload_bytes(main_space["id"], filename, body)
        documents.append(result)
        print(json.dumps({"uploaded": result}, ensure_ascii=False), flush=True)

    analysis = create_analysis_dataset(platform, analysis_space["id"])
    print(
        json.dumps(
            {
                "main_space": main_space,
                "analysis_space": analysis_space,
                "documents": documents,
                "analysis": analysis,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
