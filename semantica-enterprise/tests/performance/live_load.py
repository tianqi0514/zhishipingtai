#!/usr/bin/env python3
"""Live 50-search and five-Agent-session concurrency acceptance."""

from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import time
import urllib.request


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")


def request(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 600) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def stream_turn(conversation_id: str, token: str) -> tuple[int, float]:
    started = time.perf_counter()
    req = urllib.request.Request(
        f"{API}/conversations/{conversation_id}/messages",
        data=json.dumps({"content": "NexusOne 的主要定位是什么？"}, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    event_types: list[str] = []
    with urllib.request.urlopen(req, timeout=900) as response:
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if line.startswith("event:"):
                event_types.append(line[6:].strip())
    assert event_types and event_types[-1] == "turn_completed", event_types[-5:]
    assert "retrieval_ranked" in event_types
    return len(event_types), time.perf_counter() - started


def main() -> None:
    token = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})["access_token"]
    spaces = request("GET", "/spaces", token)
    selected = next(item for item in spaces if item.get("code") == "m10-acceptance")
    search_body = {
        "query": "NexusOne enterprise knowledge",
        "space_ids": [selected["id"]],
        "top_k": 5,
        "use_keyword": True,
        "use_vector": False,
        "use_graph": True,
        "use_reranker": False,
    }
    request("POST", "/search", token, search_body)

    def one_search(index: int) -> float:
        started = time.perf_counter()
        result = request("POST", "/search", token, {**search_body, "query": f"NexusOne enterprise knowledge {index % 5}"})
        assert result["items"]
        assert [item["rank"] for item in result["items"]] == list(range(1, len(result["items"]) + 1))
        return time.perf_counter() - started

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        search_latencies = list(executor.map(one_search, range(50)))

    conversations = [
        request(
            "POST",
            "/conversations",
            token,
            {"title": f"并发 Agent {index + 1}", "space_ids": [selected["id"]], "top_k": 3},
        )
        for index in range(5)
    ]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            agent_results = list(executor.map(lambda item: stream_turn(item["id"], token), conversations))
        for conversation in conversations:
            detail = request("GET", f"/conversations/{conversation['id']}", token)
            assistant = next(item for item in reversed(detail["messages"]) if item["role"] == "assistant")
            assert assistant["status"] == "completed"
            assert assistant["content"].strip()
            assert assistant["traces"]
    finally:
        for conversation in conversations:
            request("DELETE", f"/conversations/{conversation['id']}", token)

    sorted_latencies = sorted(search_latencies)
    print(
        json.dumps(
            {
                "search_requests": 50,
                "search_successes": len(search_latencies),
                "search_median_ms": round(statistics.median(search_latencies) * 1000),
                "search_p95_ms": round(sorted_latencies[47] * 1000),
                "agent_sessions": 5,
                "agent_successes": len(agent_results),
                "agent_max_seconds": round(max(item[1] for item in agent_results), 2),
                "agent_event_counts": [item[0] for item in agent_results],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
