#!/usr/bin/env python3
"""Live source deduplication, versioning and chunk reuse acceptance test."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
IMAGE = os.getenv("APP_IMAGE", "semantica-enterprise:0.10.0")
VOLUME = os.getenv("APPLICATION_DATA_VOLUME", "semantica-enterprise_application-data")


def request(method: str, path: str, token: str, body: dict | None = None, timeout: int = 120) -> dict:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def login() -> str:
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())["access_token"]


def update_fixture(relative: str, action: str) -> None:
    subprocess.run(
        [
            "docker", "run", "--rm", "--user", "root",
            "-v", f"{VOLUME}:/app/data",
            "-v", f"{ROOT}/tests/fixtures/update_source_volume.py:/fixture.py:ro",
            "--entrypoint", "python", IMAGE, "/fixture.py", f"/app/data/sources/{relative}", action,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def wait_job(token: str, job_id: str, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request("GET", f"/jobs/{job_id}", token)
        if job["status"] in {"succeeded", "failed"}:
            if job["status"] != "succeeded":
                raise AssertionError(f"job {job_id} failed: {job.get('error_code')} {job.get('error_message')}")
            return job
        time.sleep(2)
    raise TimeoutError(f"job {job_id} did not finish")


def wait_process_job(token: str, version_id: str, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = request("GET", "/jobs", token)
        candidate = next(
            (
                item for item in jobs
                if item.get("job_type") == "process_knowledge"
                and (item.get("input") or {}).get("version_id") == version_id
            ),
            None,
        )
        if candidate:
            return wait_job(token, candidate["id"], timeout=max(1, int(deadline - time.monotonic())))
        time.sleep(2)
    raise TimeoutError(f"process job for {version_id} was not created")


def main() -> int:
    token = login()
    spaces = request("GET", "/spaces", token)
    selected = next((item for item in spaces if item.get("code") == "m10-acceptance"), None)
    if selected is None:
        raise AssertionError("缺少 m10-acceptance 验收知识空间")
    relative = f"incremental-{secrets.token_hex(5)}"
    source_id: str | None = None
    document_id: str | None = None
    try:
        update_fixture(relative, "phase1")
        source = request(
            "POST",
            "/sources",
            token,
            {
                "space_id": selected["id"],
                "name": "增量同步自动化验收",
                "source_type": "local_dir",
                "config": {"path": f"/app/data/sources/{relative}", "recursive": True, "max_files": 10},
                "enabled": True,
            },
        )
        source_id = source["id"]

        first_sync = wait_job(token, request("POST", f"/sources/{source_id}/sync", token)["id"])
        first_version = first_sync["result"]["version_id"]
        document_id = first_sync["result"]["document_id"]
        wait_job(token, first_sync["result"]["parse_job_id"])
        first_process = wait_process_job(token, first_version)

        unchanged = wait_job(token, request("POST", f"/sources/{source_id}/sync", token)["id"])
        assert unchanged["result"].get("unchanged") is True, unchanged
        assert unchanged["result"]["version_id"] == first_version

        update_fixture(relative, "phase2")
        changed_sync = wait_job(token, request("POST", f"/sources/{source_id}/sync", token)["id"])
        second_version = changed_sync["result"]["version_id"]
        assert second_version != first_version
        wait_job(token, changed_sync["result"]["parse_job_id"])
        second_process = wait_process_job(token, second_version)

        document = request("GET", f"/documents/{document_id}", token)
        assert len(document["versions"]) == 2
        assert document["current_version_id"] == second_version
        second_chunks = request("GET", f"/versions/{second_version}/chunks?limit=500", token)["items"]
        reused = [item for item in second_chunks if (item.get("source_span") or {}).get("incremental") == "unchanged"]
        changed = [item for item in second_chunks if (item.get("source_span") or {}).get("incremental") == "changed"]
        assert reused, second_chunks
        assert changed, second_chunks
        semantic_step = next(item for item in second_process["steps"] if item["name"] == "semantic_extract")
        assert semantic_step["detail"]["reused_chunks"] >= 1
        assert semantic_step["detail"]["model_chunks"] >= 1

        current_search = request(
            "POST",
            "/search",
            token,
            {"query": "source revision graph retrieval", "space_ids": [selected["id"]], "top_k": 20},
        )
        test_items = [item for item in current_search["items"] if item.get("document_id") == document_id]
        assert test_items
        assert {item["version_id"] for item in test_items} == {second_version}
        historical_chunk_id = test_items[0]["chunk_id"]
        deleted_id = document_id
        deletion = request("DELETE", f"/documents/{deleted_id}", token)
        assert deletion["ok"] is True
        assert not deletion.get("warnings"), deletion
        document_id = None
        after_delete = request(
            "POST",
            "/search",
            token,
            {"query": "source revision graph retrieval", "space_ids": [selected["id"]], "top_k": 20},
        )
        assert all(item.get("document_id") != deleted_id for item in after_delete["items"]), after_delete
        historical_fragment = request("GET", f"/fragments/{historical_chunk_id}", token)
        assert historical_fragment["document_deleted"] is True
        assert historical_fragment["text"].strip()
        print(
            json.dumps(
                {
                    "first_version": first_version,
                    "unchanged": True,
                    "second_version": second_version,
                    "reused_chunks": len(reused),
                    "changed_chunks": len(changed),
                    "reused_extractions": semantic_step["detail"]["reused_chunks"],
                    "model_chunks": semantic_step["detail"]["model_chunks"],
                    "current_index_only": True,
                    "deleted_document_filtered": True,
                    "historical_fragment_traceable": True,
                    "deletion_publication": deletion.get("publication"),
                    "first_release": first_process["result"].get("index_release"),
                    "second_release": second_process["result"].get("index_release"),
                },
                ensure_ascii=False,
            )
        )
    finally:
        if source_id:
            try:
                request("DELETE", f"/sources/{source_id}", token)
            except Exception:
                pass
        if document_id:
            try:
                request("DELETE", f"/documents/{document_id}", token)
            except Exception:
                pass
        update_fixture(relative, "remove")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, urllib.error.HTTPError) as exc:
        print(f"source_incremental failed: {exc}", file=sys.stderr)
        raise
