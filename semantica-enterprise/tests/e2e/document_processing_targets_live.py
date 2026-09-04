#!/usr/bin/env python3
"""Validate document search/graph processing targets against the live stack.

The script uses public APIs and leaves one small acceptance space in place so
the three modes can be compared in the browser after the run. Credentials are
read only from the process environment and are never printed.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx


API = os.getenv("API_BASE", "http://api:8080/api/v1").rstrip("/")
USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
SPACE_CODE = "processing-targets-acceptance"
DOCUMENTS = {
    "vector": (
        "加工方式验收-仅检索.txt",
        "检索专属编号 VEC-90817。向量灯塔用于验证全文与向量检索，不生成知识图谱关系。",
    ),
    "graph": (
        "加工方式验收-仅图谱.txt",
        "图谱专属编号 GRAPH-61429。星海供应商供应星河处理器，星河处理器用于智慧采购演示项目。",
    ),
    "both": (
        "加工方式验收-同时加工.txt",
        "综合专属编号 BOTH-37152。云舟科技研发苍穹知识引擎，苍穹知识引擎服务国联集团知识平台。",
    ),
}


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        login = self.call("POST", "/auth/login", json={"username": USERNAME, "password": PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1200]}")
        return response.json() if response.content else {}

    def wait_job(self, job_id: str, timeout: int = 900, retry_partial: bool = True) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.call("GET", f"/jobs/{job_id}")
            if last["status"] in {"succeeded", "failed", "partial_failed", "cancelled"}:
                retryable_extraction = last["status"] in {"failed", "partial_failed"} and last.get("error_code") == "SEMANTIC_EXTRACTION_PARTIAL"
                if retryable_extraction and retry_partial:
                    retried = self.call("POST", f"/jobs/{job_id}/retry")
                    return self.wait_job(retried["id"], timeout=max(1, int(deadline - time.monotonic())), retry_partial=False)
                if last["status"] != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:4000])
                return last
            time.sleep(1.5)
        raise TimeoutError(f"job {job_id} did not finish: {last}")

    def wait_knowledge(self, version_id: str, timeout: int = 900) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for job in self.call("GET", "/jobs"):
                if (
                    job.get("job_type") == "process_knowledge"
                    and (job.get("input") or {}).get("version_id") == version_id
                ):
                    return self.wait_job(job["id"], max(1, int(deadline - time.monotonic())))
            time.sleep(1)
        raise TimeoutError(f"knowledge job for {version_id} was not created")


def step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in job["steps"] if item["name"] == name)


def search(platform: Platform, space_id: str, query: str, **channels: bool) -> dict[str, Any]:
    return platform.call(
        "POST",
        "/search",
        json={
            "query": query,
            "space_ids": [space_id],
            "top_k": 10,
            "use_keyword": channels.get("keyword", False),
            "use_vector": channels.get("vector", False),
            "use_graph": channels.get("graph", False),
            "use_reranker": False,
            "filters": {},
        },
    )


def main() -> int:
    platform = Platform()
    spaces = {item["code"]: item for item in platform.call("GET", "/spaces")}
    space = spaces.get(SPACE_CODE)
    if space is None:
        space = platform.call(
            "POST",
            "/spaces",
            json={
                "code": SPACE_CODE,
                "name": "知识加工方式验收空间",
                "description": "用于演示仅检索、仅图谱和同时加工的真实差异。",
            },
        )

    titles = {value[0] for value in DOCUMENTS.values()}
    for document in platform.call("GET", f"/documents?space_id={space['id']}"):
        if document["title"] in titles:
            platform.call("DELETE", f"/documents/{document['id']}")

    completed: dict[str, dict[str, Any]] = {}
    for mode, (filename, content) in DOCUMENTS.items():
        uploaded = platform.call(
            "POST",
            "/documents/upload",
            data={"space_id": space["id"], "knowledge_processing_mode": mode},
            files={"file": (filename, content.encode("utf-8"), "text/plain")},
        )
        platform.wait_job(uploaded["job"]["id"])
        knowledge_job = platform.wait_knowledge(uploaded["version"]["id"])
        detail = platform.call("GET", f"/documents/{uploaded['document']['id']}")
        version = next(item for item in detail["versions"] if item["id"] == uploaded["version"]["id"])
        chunks = platform.call("GET", f"/versions/{version['id']}/chunks?limit=100")["items"]
        assert knowledge_job["input"]["knowledge_processing_mode"] == mode
        assert knowledge_job["result"]["knowledge_processing_mode"] == mode
        assert set(version["parse_summary"]["knowledge_targets_completed"]) == (
            {"vector", "graph"} if mode == "both" else {mode}
        )
        completed[mode] = {
            "document_id": uploaded["document"]["id"],
            "version_id": version["id"],
            "chunk_ids": {item["id"] for item in chunks},
            "job": knowledge_job,
        }

    vector_job = completed["vector"]["job"]
    assert step(vector_job, "semantic_extract")["detail"]["skipped"] is True
    assert step(vector_job, "semantic_extract")["detail"]["reason"] == "processing_mode_excludes_graph"
    assert step(vector_job, "graph_publish")["detail"]["skipped"] is True
    assert not step(vector_job, "index_publish")["detail"].get("skipped")

    graph_job = completed["graph"]["job"]
    assert not step(graph_job, "semantic_extract")["detail"].get("skipped")
    assert step(graph_job, "semantic_extract")["detail"]["model_requests"] >= 1
    assert not step(graph_job, "graph_publish")["detail"].get("skipped")
    assert step(graph_job, "index_publish")["detail"]["skipped"] is True
    assert step(graph_job, "index_publish")["detail"]["reason"] == "processing_mode_excludes_vector"

    both_job = completed["both"]["job"]
    assert not step(both_job, "semantic_extract")["detail"].get("skipped")
    assert not step(both_job, "graph_publish")["detail"].get("skipped")
    assert not step(both_job, "index_publish")["detail"].get("skipped")

    vector_result = search(platform, space["id"], "VEC-90817", keyword=True, vector=True)
    assert any(item["document_id"] == completed["vector"]["document_id"] for item in vector_result["items"])

    graph_leak = search(platform, space["id"], "GRAPH-61429", keyword=True)
    assert all(item["document_id"] != completed["graph"]["document_id"] for item in graph_leak["items"])

    vector_graph_leak = search(platform, space["id"], "VEC-90817", graph=True)
    assert all(item["document_id"] != completed["vector"]["document_id"] for item in vector_graph_leak["items"])

    graph_result = search(platform, space["id"], "星海供应商", graph=True)
    assert any(item["document_id"] == completed["graph"]["document_id"] for item in graph_result["items"])

    both_search = search(
        platform,
        space["id"],
        "苍穹知识引擎",
        keyword=True,
        vector=True,
        graph=True,
    )
    both_items = [item for item in both_search["items"] if item["document_id"] == completed["both"]["document_id"]]
    assert both_items
    assert any("graph" in item["channels"] for item in both_items)
    assert any(set(item["channels"]) & {"keyword", "vector"} for item in both_items)

    facts = platform.call("GET", f"/knowledge/facts?space_id={space['id']}&limit=500")["items"]
    assert not any(item.get("source_chunk_id") in completed["vector"]["chunk_ids"] for item in facts)
    assert any(item.get("source_chunk_id") in completed["graph"]["chunk_ids"] for item in facts)
    assert any(item.get("source_chunk_id") in completed["both"]["chunk_ids"] for item in facts)

    report = {
        "space": space["name"],
        "space_id": space["id"],
        "modes": {
            mode: {
                "document_id": item["document_id"],
                "elapsed_seconds": round(
                    (
                        __import__("datetime").datetime.fromisoformat(item["job"]["finished_at"])
                        - __import__("datetime").datetime.fromisoformat(item["job"]["started_at"])
                    ).total_seconds(),
                    2,
                ),
                "semantic_model_requests": step(item["job"], "semantic_extract")["detail"].get("model_requests", 0),
            }
            for mode, item in completed.items()
        },
        "checks": "passed",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
