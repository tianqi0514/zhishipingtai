#!/usr/bin/env python3
"""Repeatable P4 smoke load for the Miaobi workspace.

The script uses real HTTP sessions and cleans up the temporary 100-block
document. Credentials are read by ``DemoClient`` from environment or the
project secret file and are never printed.
"""

from __future__ import annotations

import concurrent.futures
import json
import statistics
import time
import uuid

from scripts.miaobi.demo_client import DemoClient, PROJECT_CODE


def one_workspace_user(_: int) -> float:
    started = time.perf_counter()
    api = DemoClient()
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    assert project
    project_id = project["id"]
    api.get(f"/writing/projects/{project_id}")
    api.get(f"/writing/projects/{project_id}/facts")
    api.get(f"/writing/projects/{project_id}/plans")
    documents = api.get(f"/writing/projects/{project_id}/documents")
    assert documents
    api.post(f"/writing/documents/{documents[0]['id']}/validate")
    return time.perf_counter() - started


def main() -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        latencies = list(executor.map(one_workspace_user, range(10)))

    api = DemoClient()
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    assert project
    blocks = [
        {
            "id": f"load-block-{index}",
            "type": "p",
            "children": [{"text": f"生产负载验收段落 {index + 1}"}],
        }
        for index in range(100)
    ]
    document = api.post(
        "/writing/documents",
        {
            "project_id": project["id"],
            "title": f"P4 百块文稿负载验收 {uuid.uuid4().hex[:8]}",
            "content": blocks,
        },
    )
    try:
        started = time.perf_counter()
        version = api.post(
            f"/writing/documents/{document['id']}/versions",
            {"content": blocks, "change_summary": "100 块文稿负载验收"},
        )
        validation = api.post(f"/writing/documents/{document['id']}/validate")
        hundred_block_seconds = time.perf_counter() - started
        assert len(version["content"]) == 100
        assert not validation["issues"]
    finally:
        api.delete(f"/writing/documents/{document['id']}")

    ordered = sorted(latencies)
    print(
        json.dumps(
            {
                "concurrent_workspace_users": 10,
                "successes": len(latencies),
                "median_ms": round(statistics.median(latencies) * 1000),
                "p95_ms": round(ordered[-1] * 1000),
                "hundred_block_document": "passed",
                "hundred_block_save_validate_ms": round(hundred_block_seconds * 1000),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
