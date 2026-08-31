#!/usr/bin/env python3
"""Live authorization and short-lived Agent credential boundary checks."""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
SERVICE_SECRET_FILE = Path(os.getenv("AGENT_SERVICE_SECRET_FILE", "deploy/secrets/agent_service_secret"))


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    expected: int = 200,
) -> dict:
    values = {"Accept": "application/json", **(headers or {})}
    if token:
        values["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        values["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, headers=values, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            status_code = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        raw = exc.read()
    if status_code != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {status_code}: {raw[:300]!r}")
    return json.loads(raw or b"{}")


def main() -> int:
    login = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})
    admin_token = login["access_token"]
    spaces = request("GET", "/spaces", token=admin_token)
    selected = next((item for item in spaces if item.get("code") == "m10-acceptance"), None)
    if selected is None:
        raise AssertionError("缺少 m10-acceptance 验收知识空间")
    space_id = selected["id"]

    search = request(
        "POST",
        "/search",
        token=admin_token,
        body={"query": "NexusOne", "space_ids": [space_id], "top_k": 3},
    )
    if not search.get("items"):
        raise AssertionError("验收空间没有可用于权限测试的知识片段")
    chunk_id = search["items"][0].get("chunk_db_id") or search["items"][0].get("id")

    username = f"security-{secrets.token_hex(5)}"
    password = f"Boundary@{secrets.token_hex(8)}"
    user_id: str | None = None
    conversation_id: str | None = None
    try:
        created_user = request(
            "POST",
            "/users",
            token=admin_token,
            body={
                "username": username,
                "password": password,
                "display_name": "安全边界测试用户",
                "is_admin": False,
                "enabled": True,
                "role_ids": [],
            },
        )
        user_id = created_user["id"]
        user_token = request(
            "POST", "/auth/login", body={"username": username, "password": password}
        )["access_token"]
        conversation = request(
            "POST",
            "/conversations",
            token=admin_token,
            body={"title": "安全边界验证", "space_ids": [space_id]},
        )
        conversation_id = conversation["id"]

        request("GET", f"/conversations/{conversation_id}", token=user_token, expected=404)
        request(
            "POST",
            "/search",
            token=user_token,
            body={"query": "NexusOne", "space_ids": [space_id], "top_k": 3},
            expected=403,
        )
        request("GET", f"/fragments/{chunk_id}", token=user_token, expected=403)
        request(
            "POST",
            "/internal/agent/credentials",
            body={"harness_session_id": conversation["harness_session_id"]},
            headers={"X-Agent-Service-Secret": "deliberately-invalid-service-secret"},
            expected=401,
        )

        service_secret = SERVICE_SECRET_FILE.read_text(encoding="utf-8").strip()
        credential = request(
            "POST",
            "/internal/agent/credentials",
            body={"harness_session_id": conversation["harness_session_id"]},
            headers={"X-Agent-Service-Secret": service_secret},
        )
        agent_token = credential["access_token"]
        request(
            "POST",
            "/internal/agent/knowledge/search",
            token=agent_token,
            body={
                "conversation_id": conversation_id,
                "query": "NexusOne",
                "space_ids": [space_id],
                "top_k": 2,
            },
        )
        request(
            "POST",
            "/internal/agent/knowledge/search",
            token=agent_token,
            body={
                "conversation_id": "00000000-0000-0000-0000-000000000000",
                "query": "NexusOne",
                "space_ids": [space_id],
                "top_k": 2,
            },
            expected=403,
        )
        request(
            "POST",
            "/internal/agent/knowledge/search",
            token=agent_token,
            body={
                "conversation_id": conversation_id,
                "query": "NexusOne",
                "space_ids": ["00000000-0000-0000-0000-000000000000"],
                "top_k": 2,
            },
            expected=403,
        )
        request(
            "GET",
            f"/internal/agent/knowledge/spaces?conversation_id={conversation_id}",
            token="forged.jwt.value",
            expected=401,
        )

        request("DELETE", f"/conversations/{conversation_id}", token=admin_token)
        request(
            "GET",
            f"/internal/agent/knowledge/spaces?conversation_id={conversation_id}",
            token=agent_token,
            expected=401,
        )
        conversation_id = None
        print(
            json.dumps(
                {
                    "conversation_isolation": True,
                    "space_isolation": True,
                    "fragment_isolation": True,
                    "service_authentication": True,
                    "agent_scope": True,
                    "credential_revocation": True,
                },
                ensure_ascii=False,
            )
        )
    finally:
        if conversation_id:
            request("DELETE", f"/conversations/{conversation_id}", token=admin_token)
        if user_id:
            request("DELETE", f"/users/{user_id}", token=admin_token)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"security_boundaries failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
