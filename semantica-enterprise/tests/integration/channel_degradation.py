#!/usr/bin/env python3
"""Validate that a failed retrieval channel produces warnings, not an HTTP 500."""

from __future__ import annotations

import json
import os
import urllib.request


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")


def request(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 120) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def main() -> None:
    token = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})["access_token"]
    space = next(item for item in request("GET", "/spaces", token) if item.get("code") == "m10-acceptance")
    result = request(
        "POST",
        "/search",
        token,
        {
            "query": "NexusOne",
            "space_ids": [space["id"]],
            "top_k": 5,
            "use_keyword": True,
            "use_vector": os.getenv("USE_VECTOR", "0") == "1",
            "use_graph": True,
            "use_reranker": False,
        },
        timeout=180,
    )
    expected = os.environ["EXPECTED_WARNING"]
    assert result["items"], result
    assert any(expected in warning for warning in result["warnings"]), result["warnings"]
    print(json.dumps({"items": len(result["items"]), "warnings": result["warnings"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
