#!/usr/bin/env python3
"""Live four-turn Harness quality test against a running Docker stack."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
KEEP = os.getenv("KEEP_CONVERSATION", "0") == "1"
SPACE_CODE = os.getenv("TEST_SPACE_CODE", "m10-acceptance")


def request(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 300):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw or b"{}")


def stream_turn(conversation_id: str, token: str, content: str) -> list[tuple[str, dict]]:
    req = urllib.request.Request(
        f"{API}/conversations/{conversation_id}/messages",
        data=json.dumps({"content": content}, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    events: list[tuple[str, dict]] = []
    event_type = "message"
    data_lines: list[str] = []
    with urllib.request.urlopen(req, timeout=600) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif not line and data_lines:
                events.append((event_type, json.loads("\n".join(data_lines))))
                event_type = "message"
                data_lines = []
    return events


def main() -> int:
    login = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})
    token = login["access_token"]
    spaces = request("GET", "/spaces", token)
    selected = next((item for item in spaces if item.get("code") == SPACE_CODE), None)
    if selected is None:
        raise AssertionError(f"缺少 {SPACE_CODE} 验收知识空间")
    conversation = request(
        "POST",
        "/conversations",
        token,
        {
            "title": "Harness 四轮自动化验收",
            "space_ids": [selected["id"]],
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": True,
            "top_k": 5,
        },
    )
    conversation_id = conversation["id"]
    questions = [
        "该产品的主要定位是什么？",
        "它支持哪些数据源？",
        "把其中适合集团知识库的能力按优先级排序。",
        "你上面第二项的依据在哪一页？",
    ]
    results = []
    try:
        for index, question in enumerate(questions, start=1):
            live_events = stream_turn(conversation_id, token, question)
            terminal = [name for name, _ in live_events if name.startswith("turn_")]
            assert terminal and terminal[-1] == "turn_completed", (index, terminal, live_events[-3:])
            detail = request("GET", f"/conversations/{conversation_id}", token)
            assistant = [item for item in detail["messages"] if item["role"] == "assistant"][-1]
            answer = assistant["content"].strip()
            assert answer, f"第 {index} 轮回答为空"
            assert assistant["status"] == "completed", assistant
            ranked = [
                item for item in detail["events"]
                if item["message_id"] == assistant["id"] and item["event_type"] == "retrieval_ranked"
            ]
            assert ranked, f"第 {index} 轮未通过 Harness knowledge_search 检索"
            assert assistant["traces"], f"第 {index} 轮缺少检索轨迹"
            cited_numbers = {int(value) for value in re.findall(r"\[(\d{1,3})\]", answer)}
            available = {item["citation_number"] for item in assistant["citations"]}
            assert cited_numbers <= available, (index, cited_numbers, available)
            for citation in assistant["citations"]:
                fragment = request("GET", f"/fragments/{citation['chunk_id']}", token)
                assert fragment["text"].strip()
                assert fragment["document_title"]
            if index == 4:
                assert "页" in answer or "结构" in answer, answer
            results.append(
                {
                    "turn": index,
                    "events": len(live_events),
                    "searches": len(ranked),
                    "traces": len(assistant["traces"]),
                    "citations": len(assistant["citations"]),
                    "answer_chars": len(answer),
                }
            )
        refreshed = request("GET", f"/conversations/{conversation_id}", token)
        assert len([item for item in refreshed["messages"] if item["role"] == "user"]) == 4
        assert len([item for item in refreshed["messages"] if item["role"] == "assistant"]) == 4
        print(json.dumps({"conversation_id": conversation_id, "turns": results, "history_restored": True}, ensure_ascii=False))
    finally:
        if not KEEP:
            request("DELETE", f"/conversations/{conversation_id}", token)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, urllib.error.HTTPError) as exc:
        print(f"conversation_agent_quality failed: {exc}", file=sys.stderr)
        raise
