#!/usr/bin/env python3
"""Seed and verify the persistent 国联集团·智慧流程中枢 customer demo."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
from minio import Minio


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.environ["ADMIN_PASSWORD"]
MATERIALS = Path(os.getenv("SMART_PROCESS_MATERIALS_ROOT", "/tmp/smart-process-materials"))
FIXTURES = Path(os.getenv("SMART_PROCESS_FIXTURES_ROOT", "/tmp/smart-process-demo-fixtures"))
SPACE_CODE = "gl-smart-process-demo"
SPACE_NAME = "国联集团·智慧流程中枢演示库"

MATERIAL_FILES = (
    "锡国联发〔2023〕74号关于印发《国联集团本部采购实施细则》的通知.md",
    "锡国联发〔2023〕75号附件：国联集团采购管理制度.md",
    "7-锡国联党发〔2023〕59号关于印发《国联集团“三重一大”决策制度实施办法》的通知.md",
    "关键流程及合规控制节点梳理表-企业平台采购流程.xlsx",
    "测试项目 - 合规体系数字化项目/1、上会立项/国联集团上会/关于启动集团合规体系数字化项目建设的报告(1130).docx",
    "测试项目 - 合规体系数字化项目/2、采购申请/新门户&合规模块/招标文件/公示.pdf",
)


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        token = self.call("POST", "/auth/login", json={"username": USERNAME, "password": PASSWORD})["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1500]}")
        return response.json() if response.content else {}

    def wait_job(self, job_id: str, timeout: int = 2400) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.call("GET", f"/jobs/{job_id}")
            if last.get("status") in {"succeeded", "failed", "cancelled"}:
                if last["status"] != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:5000])
                return last
            time.sleep(2)
        raise TimeoutError(f"job {job_id}: {last}")

    def wait_process(self, version_id: str, timeout: int = 2400) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = next(
                (
                    item for item in self.call("GET", "/jobs")
                    if item.get("job_type") == "process_knowledge"
                    and (item.get("input") or {}).get("version_id") == version_id
                ),
                None,
            )
            if job:
                return self.wait_job(job["id"], max(1, int(deadline - time.monotonic())))
            time.sleep(2)
        raise TimeoutError(f"knowledge job for {version_id} was not created")

    def send_turn(self, conversation_id: str, content: str) -> list[str]:
        event_types: list[str] = []
        current_type = "message"
        data_lines: list[str] = []
        with self.client.stream(
            "POST",
            f"/conversations/{conversation_id}/messages",
            json={"content": content},
            timeout=httpx.Timeout(30, read=900),
        ) as response:
            if not response.is_success:
                raise RuntimeError(f"发送演示问题失败：{response.status_code} {response.read().decode()[:1500]}")
            for line in response.iter_lines():
                if line.startswith("event:"):
                    current_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    event_types.append(current_type)
                    current_type = "message"
                    data_lines = []
        if "turn_completed" not in event_types:
            raise RuntimeError(f"演示问答没有完成：{event_types[-20:]}")
        return event_types


def ensure_space(platform: Platform) -> dict[str, Any]:
    existing = next((row for row in platform.call("GET", "/spaces") if row["code"] == SPACE_CODE), None)
    if existing:
        return platform.call(
            "PUT",
            f"/spaces/{existing['id']}",
            json={
                "name": SPACE_NAME,
                "description": "面向客户演示智慧流程中枢制度、项目、流程、风险和合同知识的统一治理与检索",
                "enabled": True,
            },
        )
    return platform.call(
        "POST",
        "/spaces",
        json={
            "code": SPACE_CODE,
            "name": SPACE_NAME,
            "description": "面向客户演示智慧流程中枢制度、项目、流程、风险和合同知识的统一治理与检索",
        },
    )


def upload_materials(platform: Platform, space: dict[str, Any]) -> list[dict[str, Any]]:
    existing = {row["title"]: row for row in platform.call("GET", f"/documents?space_id={space['id']}")}
    queued: list[dict[str, Any]] = []
    for relative in MATERIAL_FILES:
        path = MATERIALS / relative
        if not path.is_file():
            raise RuntimeError(f"演示原始素材不存在：{path}")
        title = path.name
        if title in existing:
            continue
        content_type = mimetypes.guess_type(title)[0] or "application/octet-stream"
        uploaded = platform.call(
            "POST",
            "/documents/upload",
            data={"space_id": space["id"]},
            files={"file": (title, path.read_bytes(), content_type)},
        )
        platform.call(
            "PUT",
            f"/documents/{uploaded['document']['id']}",
            json={"tags": ["客户演示", "智慧流程中枢", "原始业务材料"]},
        )
        queued.append({"title": title, "parse_job_id": uploaded["job"]["id"], "version_id": uploaded["version"]["id"]})
    return queued


def prepare_connector_payloads() -> None:
    if not FIXTURES.is_dir():
        raise RuntimeError(f"演示补充素材目录不存在：{FIXTURES}")
    local_root = Path("/app/data/sources/smart-process-demo")
    local_root.mkdir(parents=True, exist_ok=True)
    for source in FIXTURES.iterdir():
        if source.is_file():
            shutil.copy2(source, local_root / source.name)

    minio = Minio(
        os.environ["OBJECT_STORE_ENDPOINT"],
        access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
        secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
        secure=False,
    )
    bucket = "smart-process-demo"
    if not minio.bucket_exists(bucket):
        minio.make_bucket(bucket)
    for name in ("流程接口目录.json", "流程风险事件.jsonl"):
        source = FIXTURES / name
        minio.fput_object(bucket, f"knowledge/{name}", str(source))


def source_definitions(space_id: str) -> list[dict[str, Any]]:
    return [
        {"name": "演示·智慧流程运行门户", "source_type": "web", "config": {"url": "http://source-fixture:8088/portal", "respect_robots": True}},
        {"name": "演示·流程实例 REST API", "source_type": "rest", "config": {"url": "http://source-fixture:8088/api/processes", "method": "GET"}},
        {"name": "演示·流程风险 RSS", "source_type": "rss", "config": {"url": "http://source-fixture:8088/feed.xml", "max_items": 10}},
        {"name": "演示·流程知识 Sitemap", "source_type": "sitemap", "config": {"url": "http://source-fixture:8088/sitemap.xml", "max_urls": 10, "respect_robots": True}},
        {"name": "演示·业务材料挂载目录", "source_type": "local_dir", "config": {"path": "/app/data/sources/smart-process-demo", "recursive": True, "max_files": 20}},
        {
            "name": "演示·MinIO 流程对象库",
            "source_type": "s3",
            "config": {
                "endpoint": os.environ["OBJECT_STORE_ENDPOINT"],
                "bucket": "smart-process-demo",
                "prefix": "knowledge/",
                "access_key": os.environ["OBJECT_STORE_ACCESS_KEY"],
                "secure": False,
                "max_files": 20,
            },
            "secret": os.environ["OBJECT_STORE_SECRET_KEY"],
        },
    ]


def ensure_sources(platform: Platform, space: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = {
        row["name"]: row
        for row in platform.call("GET", f"/sources?space_id={space['id']}")
    }
    sources: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    documents = platform.call("GET", f"/documents?space_id={space['id']}")
    queued: list[dict[str, Any]] = []
    for definition in source_definitions(space["id"]):
        payload = {"space_id": space["id"], "enabled": True, **definition}
        source = existing.get(definition["name"])
        if source:
            source = platform.call("PUT", f"/sources/{source['id']}", json=payload)
        else:
            source = platform.call("POST", "/sources", json=payload)
        tested = platform.call(
            "POST",
            "/sources/test",
            json={"source_id": source["id"], "source_type": source["source_type"], "config": source["config"]},
        )
        sources.append(source)
        tests.append({"name": source["name"], "status": tested["status"], "bytes": tested["bytes"]})
        if not any(row.get("source_id") == source["id"] for row in documents):
            queued.append({"source": source, "job": platform.call("POST", f"/sources/{source['id']}/sync")})
    return tests, queued


def wait_pipeline(platform: Platform, uploaded: list[dict[str, Any]], source_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in uploaded:
        parse = platform.wait_job(item["parse_job_id"])
        knowledge = platform.wait_process(item["version_id"])
        results.append({"name": item["title"], "parse": parse["status"], "knowledge": knowledge["status"]})
    for item in source_jobs:
        sync = platform.wait_job(item["job"]["id"])
        result = sync.get("result") or {}
        platform.wait_job(result["parse_job_id"])
        knowledge = platform.wait_process(result["version_id"])
        results.append({"name": item["source"]["name"], "sync": sync["status"], "knowledge": knowledge["status"]})
    return results


def tag_demo_documents(platform: Platform, space_id: str) -> None:
    source_names = {
        row["id"]: row["name"]
        for row in platform.call("GET", f"/sources?space_id={space_id}")
    }
    for document in platform.call("GET", f"/documents?space_id={space_id}"):
        if document.get("source_id"):
            tags = ["客户演示", "智慧流程中枢", "数据源同步", source_names.get(document["source_id"], "外部数据源")]
        else:
            tags = ["客户演示", "智慧流程中枢", "原始业务材料"]
        platform.call("PUT", f"/documents/{document['id']}", json={"tags": tags})


def pick_evidence_chunk(platform: Platform, space_id: str) -> str:
    documents = platform.call("GET", f"/documents?space_id={space_id}")
    preferred = sorted(documents, key=lambda row: "业务材料挂载目录" not in row["title"])
    for document in preferred:
        version_id = document.get("current_version_id")
        if not version_id:
            continue
        chunks = platform.call("GET", f"/versions/{version_id}/chunks?limit=500").get("items", [])
        for chunk in chunks:
            if "智慧流程中枢一期贯通上会立项、采购申请、合同签订" in chunk.get("text", ""):
                return chunk["id"]
    raise RuntimeError("未找到可用于图谱溯源的演示片段")


def ensure_graph(platform: Platform, space_id: str, source_chunk_id: str) -> dict[str, int]:
    entity_specs = {
        "智慧流程中枢": "平台",
        "上会立项": "流程阶段",
        "采购申请": "流程阶段",
        "合同签订": "流程阶段",
        "国联数字科技有限公司": "组织",
        "智慧流程中枢一期": "项目",
        "GL-SP-2026-002": "流程实例",
        "黄色超时预警": "风险事件",
    }
    existing_entities: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = platform.call("GET", f"/knowledge/entities?space_id={space_id}&offset={offset}&limit=500")
        existing_entities.extend(page.get("items", []))
        offset += len(page.get("items", []))
        if offset >= int(page.get("total") or 0) or not page.get("items"):
            break
    entities: dict[str, dict[str, Any]] = {}
    for name, entity_type in entity_specs.items():
        row = next(
            (
                item for item in existing_entities
                if item["canonical_name"].casefold() == name.casefold() and item["entity_type"] == entity_type
            ),
            None,
        )
        if row is None:
            row = platform.call(
                "POST",
                "/knowledge/entities",
                json={"space_id": space_id, "canonical_name": name, "entity_type": entity_type, "confidence": 0.99},
            )
        entities[name] = row

    relation_specs = [
        ("智慧流程中枢", "贯通", "上会立项"),
        ("智慧流程中枢", "贯通", "采购申请"),
        ("智慧流程中枢", "贯通", "合同签订"),
        ("国联数字科技有限公司", "建设", "智慧流程中枢一期"),
        ("GL-SP-2026-002", "触发", "黄色超时预警"),
    ]
    existing_facts: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = platform.call("GET", f"/knowledge/facts?space_id={space_id}&offset={offset}&limit=500")
        existing_facts.extend(page.get("items", []))
        offset += len(page.get("items", []))
        if offset >= int(page.get("total") or 0) or not page.get("items"):
            break
    created = 0
    for subject, predicate, obj in relation_specs:
        duplicate = next(
            (
                item for item in existing_facts
                if str(item.get("subject_name") or "").casefold() == subject.casefold()
                and item.get("predicate") == predicate
                and str(item.get("object_name") or "").casefold() == obj.casefold()
            ),
            None,
        )
        if duplicate:
            continue
        platform.call(
            "POST",
            "/knowledge/facts",
            json={
                "space_id": space_id,
                "subject_entity_id": entities[subject]["id"],
                "predicate": predicate,
                "object_entity_id": entities[obj]["id"],
                "source_chunk_id": source_chunk_id,
                "confidence": 0.99,
            },
        )
        created += 1
    facts = platform.call("GET", f"/knowledge/facts?space_id={space_id}&limit=500").get("items", [])
    return {"entities": len(entities), "facts": len(facts), "created_facts": created}


def verify(platform: Platform, space: dict[str, Any]) -> dict[str, Any]:
    query = "智慧流程中枢贯通哪些业务阶段"
    result = platform.call(
        "POST",
        "/search",
        json={
            "query": query,
            "space_ids": [space["id"]],
            "top_k": 10,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "filters": {},
        },
    )
    missing = [name for name in ("keyword", "vector", "graph") if result["channel_counts"].get(name, 0) < 1]
    if missing:
        raise RuntimeError(f"三路检索验收失败，缺少：{missing}；返回：{json.dumps(result, ensure_ascii=False)[:5000]}")
    exact = platform.call(
        "POST",
        "/search",
        json={
            "query": "GL-SP-2026-002 为什么预警",
            "space_ids": [space["id"]],
            "top_k": 10,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "filters": {},
        },
    )
    documents = platform.call("GET", f"/documents?space_id={space['id']}")
    profiles = [platform.call("GET", f"/documents/{item['id']}").get("profile") for item in documents]
    return {
        "space": {"id": space["id"], "code": space["code"], "name": space["name"]},
        "documents": len(documents),
        "profiles": sum(bool(item) for item in profiles),
        "primary_query": {"query": query, "channel_counts": result["channel_counts"], "results": len(result["items"]), "warnings": result["warnings"]},
        "risk_query": {"channel_counts": exact["channel_counts"], "results": len(exact["items"]), "warnings": exact["warnings"]},
    }


def ensure_demo_conversation(platform: Platform, space: dict[str, Any]) -> dict[str, Any]:
    title = "客户演示｜智慧流程中枢三轮问答"
    conversations = platform.call("GET", "/conversations").get("items", [])
    conversation = next((item for item in conversations if item["title"] == title), None)
    if conversation:
        detail = platform.call("GET", f"/conversations/{conversation['id']}")
        completed = [
            item for item in detail.get("messages", [])
            if item.get("role") == "assistant" and item.get("status") == "completed"
        ]
        if len(completed) >= 3:
            return {"id": conversation["id"], "turns": len(completed), "status": "existing"}
        platform.call("DELETE", f"/conversations/{conversation['id']}")

    conversation = platform.call(
        "POST",
        "/conversations",
        json={
            "title": title,
            "space_ids": [space["id"]],
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "top_k": 10,
        },
    )
    questions = [
        "请只依据当前知识空间回答：智慧流程中枢一期贯通哪些业务阶段？",
        "其中流程实例 GL-SP-2026-002 为什么触发预警，责任部门是谁？",
        "结合集团本部采购实施细则，采购估算价达到30万元以上应如何执行？请附引用。",
    ]
    event_counts: list[int] = []
    for question in questions:
        event_counts.append(len(platform.send_turn(conversation["id"], question)))
    detail = platform.call("GET", f"/conversations/{conversation['id']}")
    assistants = [item for item in detail.get("messages", []) if item.get("role") == "assistant"]
    if len(assistants) != 3 or any(item.get("status") != "completed" for item in assistants):
        raise RuntimeError(f"演示三轮问答状态异常：{json.dumps(assistants, ensure_ascii=False)[:3000]}")
    if any(not item.get("citations") for item in assistants):
        raise RuntimeError("演示三轮问答存在未生成可点击引用的回答")
    return {"id": conversation["id"], "turns": 3, "event_counts": event_counts, "status": "created"}


def main() -> None:
    platform = Platform()
    space = ensure_space(platform)
    prepare_connector_payloads()
    uploaded = upload_materials(platform, space)
    source_tests, source_jobs = ensure_sources(platform, space)
    pipeline = wait_pipeline(platform, uploaded, source_jobs)
    tag_demo_documents(platform, space["id"])
    evidence_chunk_id = pick_evidence_chunk(platform, space["id"])
    graph = ensure_graph(platform, space["id"], evidence_chunk_id)
    verification = verify(platform, space)
    conversation = ensure_demo_conversation(platform, space)
    print(json.dumps({"source_tests": source_tests, "pipeline": pipeline, "graph": graph, "conversation": conversation, **verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
