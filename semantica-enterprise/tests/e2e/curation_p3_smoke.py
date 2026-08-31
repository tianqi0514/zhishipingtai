#!/usr/bin/env python3
"""Live, reversible P0-P3 curation smoke against the Docker deployment."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")


def request(method: str, path: str, token: str | None = None, body: dict | None = None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=900) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode(errors='replace')}") from exc


def wait_job(token: str, job_id: str, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = request("GET", f"/jobs/{job_id}", token)
        if row["status"] == "succeeded":
            return row
        if row["status"] == "failed":
            raise RuntimeError(row.get("error_message") or "curation job failed")
        time.sleep(1)
    raise TimeoutError(job_id)


def find_processed_version(token: str) -> tuple[dict, dict, dict]:
    for document in request("GET", "/documents", token):
        detail = request("GET", f"/documents/{document['id']}", token)
        for version in detail.get("versions", []):
            if (version.get("parse_summary") or {}).get("knowledge_status") != "published":
                continue
            try:
                profile = request("GET", f"/versions/{version['id']}/profile", token)
                chunks = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)
            except RuntimeError:
                continue
            if chunks.get("items"):
                return detail, version, profile
    raise RuntimeError("没有可用于治理验证的已加工文档")


def main() -> None:
    token = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})["access_token"]
    document, version, profile = find_processed_version(token)
    space_id = document["space_id"]
    profile_decision_id = chunk_decision_id = None
    results: dict[str, object] = {}
    try:
        changed_classification = f"{profile['classification']} · P3验证"[:300]
        created = request("POST", "/curation/decisions", token, {
            "space_id": space_id,
            "target_type": "document_profile",
            "target_id": version["id"],
            "version_id": version["id"],
            "field_path": "classification",
            "operation": "override",
            "value": changed_classification,
            "scope": "version_only",
            "reason_code": "e2e_smoke",
            "auto_publish": True,
        })
        profile_decision_id = created["decision"]["id"]
        projected = request("GET", f"/versions/{version['id']}/profile", token)
        assert projected["classification"] == changed_classification
        assert projected["automatic"]["classification"] == profile["classification"]
        assert projected["field_origins"]["classification"] == "manual"
        request("POST", f"/curation/decisions/{profile_decision_id}/rollback", token)
        profile_decision_id = None

        chunk = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)["items"][0]
        created = request("POST", "/curation/decisions", token, {
            "space_id": space_id,
            "target_type": "chunk",
            "target_id": chunk["chunk_id"],
            "version_id": version["id"],
            "field_path": "boost",
            "operation": "override",
            "value": 1.2,
            "scope": "version_only",
            "reason_code": "e2e_smoke",
            "auto_publish": True,
        })
        chunk_decision_id = created["decision"]["id"]
        forward = wait_job(token, created["job"]["id"])
        projected_chunk = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)["items"][0]
        assert projected_chunk["boost"] == 1.2
        rolled_back = request("POST", f"/curation/decisions/{chunk_decision_id}/rollback", token)
        chunk_decision_id = None
        reverse = wait_job(token, rolled_back["job"]["id"])
        restored_chunk = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)["items"][0]
        assert restored_chunk["boost"] == 1.0
        results = {
            "document_id": document["id"],
            "version_id": version["id"],
            "profile_overlay_and_rollback": "passed",
            "chunk_projection_release": forward["result"],
            "chunk_rollback_release": reverse["result"],
        }
    finally:
        if chunk_decision_id:
            rolled_back = request("POST", f"/curation/decisions/{chunk_decision_id}/rollback", token)
            if rolled_back.get("job"):
                wait_job(token, rolled_back["job"]["id"])
        if profile_decision_id:
            request("POST", f"/curation/decisions/{profile_decision_id}/rollback", token)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
