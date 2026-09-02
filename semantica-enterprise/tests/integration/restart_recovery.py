#!/usr/bin/env python3
"""Live recovery checks for queued Celery work and persisted Harness sessions."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
SPACE_CODE = os.getenv("TEST_SPACE_CODE", "m10-acceptance")


def request(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 600):
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


def upload(token: str, space_id: str, filename: str, content: bytes) -> dict:
    boundary = f"chuanshen-{secrets.token_hex(12)}"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"space_id\"\r\n\r\n{space_id}\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: text/plain\r\n\r\n"
        ).encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        f"{API}/documents/upload",
        data=b"".join(parts),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read())


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def wait_health(container: str, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = subprocess.run(
            ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", container],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if value == "healthy":
            return
        time.sleep(3)
    raise TimeoutError(f"{container} did not become healthy")


def wait_job(token: str, job_id: str, timeout: int = 600) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request("GET", f"/jobs/{job_id}", token)
        if job["status"] in {"succeeded", "failed"}:
            assert job["status"] == "succeeded", job
            return job
        time.sleep(2)
    raise TimeoutError(job_id)


def persisted_harness_turns(session_id: str) -> int:
    script = """
const fs = require('node:fs')
const id = process.env.SESSION_ID
const path = `/var/lib/chuanshen-harness/sessions/--workspace--/${id}/session.jsonl`
const rows = fs.readFileSync(path, 'utf8').trim().split('\\n').map(JSON.parse)
console.log(rows.filter(row => row.type === 'turn/start').length)
""".strip()
    value = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "-e", f"SESSION_ID={session_id}",
            "agent-runtime", "node", "-e", script,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(value)


def stream_turn(token: str, conversation_id: str, content: str) -> list[str]:
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
    events: list[str] = []
    with urllib.request.urlopen(req, timeout=900) as response:
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if line.startswith("event:"):
                events.append(line[6:].strip())
    assert events and events[-1] == "turn_completed", events[-5:]
    assert "retrieval_ranked" in events
    return events


def main() -> None:
    token = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})["access_token"]
    space = next(item for item in request("GET", "/spaces", token) if item.get("code") == SPACE_CODE)
    document_id: str | None = None
    conversation_id: str | None = None
    worker_stopped = False
    try:
        compose("stop", "worker")
        worker_stopped = True
        marker = secrets.token_hex(6)
        uploaded = upload(
            token,
            space["id"],
            f"worker-recovery-{marker}.txt",
            f"NexusOne worker recovery fixture {marker}".encode(),
        )
        document_id = uploaded["document"]["id"]
        queued = request("GET", f"/jobs/{uploaded['job']['id']}", token)
        assert queued["status"] == "queued", queued
        compose("start", "worker")
        worker_stopped = False
        wait_health("semantica-enterprise-worker-1")
        completed = wait_job(token, uploaded["job"]["id"])

        conversation = request(
            "POST",
            "/conversations",
            token,
            {"title": "重启恢复验收", "space_ids": [space["id"]], "top_k": 3},
        )
        conversation_id = conversation["id"]
        first_events = stream_turn(token, conversation_id, "NexusOne 的主要定位是什么？")
        before = request("GET", f"/conversations/{conversation_id}", token)
        harness_session_id = before["harness_session_id"]
        compose("restart", "agent-runtime")
        wait_health("semantica-enterprise-agent-runtime-1")
        second_events = stream_turn(token, conversation_id, "它的主要优势是什么？")
        after = request("GET", f"/conversations/{conversation_id}", token)
        assert after["harness_session_id"] == harness_session_id
        assert len(after["messages"]) >= 4
        assistants = [item for item in after["messages"] if item["role"] == "assistant"]
        assert len(assistants) == 2 and all(item["status"] == "completed" for item in assistants)
        assert all(item["traces"] and item["citations"] for item in assistants)
        assert "NexusOne" in assistants[-1]["content"], assistants[-1]["content"]
        assert "没有可调取的上一轮" not in assistants[-1]["content"], assistants[-1]["content"]
        persisted_turns = persisted_harness_turns(harness_session_id)
        assert persisted_turns >= 2, persisted_turns
        print(json.dumps({
            "worker_queued_before_restart": queued["status"] == "queued",
            "worker_job_after_restart": completed["status"],
            "harness_session_preserved": True,
            "messages_after_restart": len(after["messages"]),
            "first_turn_events": len(first_events),
            "second_turn_events": len(second_events),
            "persisted_harness_turns": persisted_turns,
        }, ensure_ascii=False))
    finally:
        if worker_stopped:
            compose("start", "worker")
        if conversation_id:
            try:
                request("DELETE", f"/conversations/{conversation_id}", token)
            except Exception:
                pass
        if document_id:
            try:
                request("DELETE", f"/documents/{document_id}", token)
            except Exception:
                pass


if __name__ == "__main__":
    main()
