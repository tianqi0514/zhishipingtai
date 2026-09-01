#!/usr/bin/env python3
"""Live cancellation and retry test for the FastAPI → Harness SSE path."""

from __future__ import annotations

import json
import os
import atexit
import threading
import time
import urllib.request

from conversation_agent_quality import API, PASSWORD, SPACE_CODE, USERNAME, request


def stream(path: str, token: str, body: dict) -> list[tuple[str, dict]]:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    events = []
    event_type = "message"
    data_lines = []
    with urllib.request.urlopen(req, timeout=600) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif not line and data_lines:
                events.append((event_type, json.loads("\n".join(data_lines))))
                event_type, data_lines = "message", []
    return events


def main() -> None:
    token = request(
        "POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD}
    )["access_token"]
    spaces = request("GET", "/spaces", token)
    space = next(item for item in spaces if item.get("code") == SPACE_CODE)
    conversation = request(
        "POST",
        "/conversations",
        token,
        {"title": "取消重试自动化验收", "space_ids": [space["id"]], "top_k": 5},
    )
    conversation_id = conversation["id"]
    def cleanup() -> None:
        try:
            request("DELETE", f"/conversations/{conversation_id}", token)
        except Exception:
            pass
    atexit.register(cleanup)
    holder: dict[str, object] = {}

    def generate() -> None:
        try:
            holder["events"] = stream(
                f"/conversations/{conversation_id}/messages",
                token,
                {"content": "请基于知识库详细说明该平台的定位、能力、数据源和知识加工流程。"},
            )
        except Exception as exc:  # the API projection, not the client socket, is authoritative
            holder["error"] = repr(exc)

    thread = threading.Thread(target=generate, daemon=True)
    thread.start()
    generating = None
    for _ in range(100):
        detail = request("GET", f"/conversations/{conversation_id}", token)
        generating = next(
            (item for item in reversed(detail["messages"]) if item["role"] == "assistant"),
            None,
        )
        if generating and generating["status"] == "generating":
            break
        time.sleep(0.1)
    assert generating and generating["status"] == "generating", "回答未进入生成状态"
    request("POST", f"/conversations/{conversation_id}/cancel", token, {})
    thread.join(timeout=60)
    assert not thread.is_alive(), "取消后 SSE 未在 60 秒内结束"

    cancelled_detail = request("GET", f"/conversations/{conversation_id}", token)
    users_before = [item for item in cancelled_detail["messages"] if item["role"] == "user"]
    assistants_before = [item for item in cancelled_detail["messages"] if item["role"] == "assistant"]
    cancelled = assistants_before[-1]
    assert cancelled["status"] == "cancelled", cancelled

    retry_events = stream(
        f"/conversations/{conversation_id}/messages/{cancelled['id']}/retry",
        token,
        {},
    )
    assert any(name == "turn_completed" for name, _ in retry_events), retry_events[-5:]
    completed_detail = request("GET", f"/conversations/{conversation_id}", token)
    users_after = [item for item in completed_detail["messages"] if item["role"] == "user"]
    assistants_after = [item for item in completed_detail["messages"] if item["role"] == "assistant"]
    retried = assistants_after[-1]
    assert len(users_before) == len(users_after) == 1, "重试重复创建了用户问题"
    assert len(assistants_after) == 2
    assert retried["status"] == "completed"
    assert retried["message_metadata"]["retry_of"] == cancelled["id"]
    assert retried["traces"], "重试回答缺少检索轨迹"
    print(json.dumps({
        "cancelled": True,
        "retry_completed": True,
        "user_messages": len(users_after),
        "assistant_messages": len(assistants_after),
        "retry_events": len(retry_events),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
