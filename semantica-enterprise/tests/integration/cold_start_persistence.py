#!/usr/bin/env python3
"""Stop/start the complete Compose stack without volumes and verify persistence."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
SPACE_CODE = os.getenv("TEST_SPACE_CODE", "m10-acceptance")
MODEL_TURN_ATTEMPTS = max(1, int(os.getenv("MODEL_TURN_ATTEMPTS", "4")))
REQUIRED_SERVICES = {
    "agent-runtime",
    "api",
    "falkordb",
    "mcp-server",
    "minio",
    "opensearch",
    "postgres",
    "qdrant",
    "rabbitmq",
    "redis",
    "scheduler",
    "worker",
}


def request(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 900):
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


def login() -> str:
    return request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})["access_token"]


def stream_turn(
    token: str,
    conversation_id: str,
    content: str,
    retry_message_id: str | None = None,
) -> list[str]:
    path = (
        f"/conversations/{conversation_id}/messages/{retry_message_id}/retry"
        if retry_message_id
        else f"/conversations/{conversation_id}/messages"
    )
    req = urllib.request.Request(
        f"{API}{path}",
        data=None if retry_message_id else json.dumps({"content": content}, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    events: list[str] = []
    with urllib.request.urlopen(req, timeout=900) as response:
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if line.startswith("event:"):
                events.append(line[6:].strip())
    return events


def complete_turn_with_retry(token: str, conversation_id: str, content: str) -> tuple[list[str], int]:
    """Use the platform's real retry API when an external model turn is transiently unavailable."""
    failed_message_id: str | None = None
    for attempt in range(1, MODEL_TURN_ATTEMPTS + 1):
        events = stream_turn(token, conversation_id, content, failed_message_id)
        if events and events[-1] == "turn_completed":
            assert "retrieval_ranked" in events
            return events, attempt
        detail = request("GET", f"/conversations/{conversation_id}", token)
        failed = next(
            (
                message
                for message in reversed(detail["messages"])
                if message["role"] == "assistant" and message["status"] in {"failed", "cancelled"}
            ),
            None,
        )
        assert failed is not None, events[-5:]
        failed_message_id = failed["id"]
        if attempt < MODEL_TURN_ATTEMPTS:
            time.sleep(min(5 * attempt, 15))
    raise AssertionError(f"Agent turn failed after {MODEL_TURN_ATTEMPTS} attempts: {events[-5:]}")


def compose(*args: str, quiet: bool = False) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL if quiet else None,
    )


def wait_stack(timeout: int = 600) -> list[str]:
    deadline = time.monotonic() + timeout
    last: list[dict] = []
    while time.monotonic() < deadline:
        rows = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        last = [json.loads(row) for row in rows if row.strip()]
        healthy_services = {
            row["Service"]
            for row in last
            if row.get("State") == "running" and row.get("Health") == "healthy"
        }
        if REQUIRED_SERVICES <= healthy_services:
            return sorted(REQUIRED_SERVICES)
        time.sleep(3)
    raise TimeoutError({row.get("Service"): (row.get("State"), row.get("Health")) for row in last})


def migration_count() -> int:
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "semantica", "-d", "semantica",
            "-Atc", "SELECT count(*) FROM schema_migrations",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def main() -> None:
    token = login()
    spaces = request("GET", "/spaces", token)
    space = next(item for item in spaces if item.get("code") == SPACE_CODE)
    document_count = len(request("GET", f"/documents?space_id={space['id']}", token))
    migrations_before = migration_count()
    conversation = request(
        "POST",
        "/conversations",
        token,
        {"title": "全栈冷启动持久化验收", "space_ids": [space["id"]], "top_k": 3},
    )
    conversation_id = conversation["id"]
    stack_stopped = False
    try:
        first_events, first_attempts = complete_turn_with_retry(
            token, conversation_id, "NexusOne 的主要定位是什么？"
        )
        before = request("GET", f"/conversations/{conversation_id}", token)
        session_id = before["harness_session_id"]
        message_count_before = len(before["messages"])
        compose("stop", quiet=True)
        stack_stopped = True
        compose("start", quiet=True)
        services = wait_stack()
        stack_stopped = False

        token = login()
        after_restart = request("GET", f"/conversations/{conversation_id}", token)
        assert after_restart["harness_session_id"] == session_id
        assert len(after_restart["messages"]) == message_count_before
        assert len(request("GET", f"/documents?space_id={space['id']}", token)) == document_count
        assert migration_count() == migrations_before
        search = request(
            "POST",
            "/search",
            token,
            {"query": "NexusOne enterprise knowledge", "space_ids": [space["id"]], "top_k": 5},
        )
        assert search["items"] and len([value for value in search["channel_counts"].values() if value]) >= 2
        second_events, second_attempts = complete_turn_with_retry(
            token, conversation_id, "它支持哪些数据源？请结合上一轮对象回答。"
        )
        final = request("GET", f"/conversations/{conversation_id}", token)
        assistants = [item for item in final["messages"] if item["role"] == "assistant"]
        completed_assistants = [item for item in assistants if item["status"] == "completed"]
        assert len(completed_assistants) == 2
        assert "NexusOne" in assistants[-1]["content"]
        assert "没有可调取的上一轮" not in assistants[-1]["content"]
        assert assistants[-1]["traces"] and assistants[-1]["citations"]
        print(json.dumps({
            "services_healthy": len(services),
            "volumes_deleted": False,
            "documents_preserved": document_count,
            "migrations_preserved": migrations_before,
            "conversation_preserved": True,
            "harness_session_preserved": True,
            "first_turn_events": len(first_events),
            "second_turn_events": len(second_events),
            "first_turn_attempts": first_attempts,
            "second_turn_attempts": second_attempts,
            "retrieval_channels": search["channel_counts"],
        }, ensure_ascii=False))
    finally:
        if stack_stopped:
            compose("start", quiet=True)
            wait_stack()
        try:
            token = login()
            request("DELETE", f"/conversations/{conversation_id}", token)
        except Exception:
            pass


if __name__ == "__main__":
    main()
