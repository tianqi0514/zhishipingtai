#!/usr/bin/env python3
"""Live, reversible P0-P3 curation smoke against the Docker deployment."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
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
    profile_batch_id = chunk_batch_id = None
    results: dict[str, object] = {}
    try:
        changed_classification = f"{profile['classification']} · P3验证"[:300]
        created = request("POST", f"/curation/profiles/{version['id']}", token, {
            "space_id": space_id,
            "changes": {
                "classification": changed_classification,
                "tags": [*(profile.get("tags") or []), "治理工作台验证"],
            },
            "scope": "version_only",
            "reason_note": "治理工作台端到端验证",
        })
        profile_batch_id = created["batch"]["id"]
        assert created["decision_count"] == 2
        projected = request("GET", f"/versions/{version['id']}/profile", token)
        assert projected["classification"] == changed_classification
        assert projected["field_origins"]["classification"] == "manual"
        batch = request("GET", f"/curation/batches/{profile_batch_id}", token)
        assert batch["decision_count"] == 2
        assert batch["field_summary"] == "主题分类、标签"
        assert batch["display_status"] == "即时生效"
        batches = request("GET", f"/curation/batches?space_id={space_id}&limit=10", token)
        assert any(item["id"] == profile_batch_id for item in batches["items"])
        workbench = request("GET", f"/curation/workbench?space_id={space_id}&status=all", token)
        assert "summary" in workbench and "has_knowledge" in workbench
        target_query = urllib.parse.quote(document["title"])
        targets = request("GET", f"/curation/targets/search?space_id={space_id}&target_type=document_profile&query={target_query}", token)
        assert any(item["version_id"] == version["id"] for item in targets["items"])
        request("POST", f"/curation/batches/{profile_batch_id}/rollback", token)
        profile_batch_id = None
        restored_profile = request("GET", f"/versions/{version['id']}/profile", token)
        assert restored_profile["classification"] == profile["classification"]

        chunk = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)["items"][0]
        original_boost = chunk["boost"]
        changed_boost = 1.2 if original_boost != 1.2 else 1.3
        created = request("POST", "/curation/decisions", token, {
            "space_id": space_id,
            "target_type": "chunk",
            "target_id": chunk["chunk_id"],
            "version_id": version["id"],
            "field_path": "boost",
            "operation": "override",
            "value": changed_boost,
            "scope": "version_only",
            "reason_code": "e2e_smoke",
            "reason_note": "检索召回优先级验证",
            "auto_publish": True,
        })
        chunk_batch_id = created["batch"]["id"]
        forward = wait_job(token, created["job"]["id"])
        projected_chunk = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)["items"][0]
        assert projected_chunk["boost"] == changed_boost
        assert projected_chunk["automatic_boost"] == 1.0
        chunk_batch = request("GET", f"/curation/batches/{chunk_batch_id}", token)
        assert "向量检索" in chunk_batch["impacts"]
        rolled_back = request("POST", f"/curation/batches/{chunk_batch_id}/rollback", token)
        chunk_batch_id = None
        reverse = wait_job(token, rolled_back["job"]["id"])
        restored_chunk = request("GET", f"/versions/{version['id']}/chunks?limit=1", token)["items"][0]
        assert restored_chunk["boost"] == original_boost
        results = {
            "document_id": document["id"],
            "version_id": version["id"],
            "profile_overlay_and_rollback": "passed",
            "chunk_projection_release": forward["result"],
            "chunk_rollback_release": reverse["result"],
        }
    finally:
        if chunk_batch_id:
            rolled_back = request("POST", f"/curation/batches/{chunk_batch_id}/rollback", token)
            if rolled_back.get("job"):
                wait_job(token, rolled_back["job"]["id"])
        if profile_batch_id:
            request("POST", f"/curation/batches/{profile_batch_id}/rollback", token)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
