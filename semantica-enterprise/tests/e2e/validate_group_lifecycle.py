#!/usr/bin/env python3
"""Exercise current-version, curation rollback and deletion propagation on group data.

The script uses only public platform APIs.  It creates one clearly named test
document, verifies that the document reaches the current knowledge supply, and
then deletes it again.  The fixed 国联集团 acceptance dataset is never deleted.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
MARKER = "GL-LIFECYCLE-20260901-UNIQUE"
TITLE = "业务生命周期临时验证.txt"


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        login = self.call("POST", "/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1200]}")
        return response.json() if response.content else {}

    def wait_job(self, job_id: str, timeout: int = 900) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.call("GET", f"/jobs/{job_id}")
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                if job["status"] != "succeeded":
                    raise RuntimeError(f"job {job_id}: {job['status']} {job.get('error_message')}")
                return job
            time.sleep(1)
        raise TimeoutError(job_id)

    def wait_knowledge(self, version_id: str, timeout: int = 900) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for job in self.call("GET", "/jobs"):
                if job.get("job_type") == "process_knowledge" and (job.get("input") or {}).get("version_id") == version_id:
                    return self.wait_job(job["id"], max(1, int(deadline - time.monotonic())))
            time.sleep(1)
        raise TimeoutError(f"knowledge job for {version_id}")


def main() -> int:
    platform = Platform()
    spaces = {row["code"]: row for row in platform.call("GET", "/spaces")}
    space = spaces["gl-product-acceptance"]
    documents = platform.call("GET", "/documents")

    # A previous interrupted run may leave the same isolated test document.
    for document in documents:
        if document["title"] == TITLE:
            platform.call("DELETE", f"/documents/{document['id']}")

    upload = platform.call(
        "POST",
        "/documents/upload",
        data={"space_id": space["id"]},
        files={
            "file": (
                TITLE,
                f"{MARKER}\n本文件只用于验证发布、人工治理回滚和删除传播。\n".encode(),
                "text/plain",
            )
        },
    )
    document_id = upload["document"]["id"]
    version_id = upload["version"]["id"]
    batch_id: str | None = None
    deleted = False
    try:
        platform.wait_job(upload["job"]["id"])
        platform.wait_knowledge(version_id)

        search = platform.call("POST", "/search", json={
            "query": MARKER,
            "space_ids": [space["id"]],
            "top_k": 10,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "filters": {},
        })
        assert any(row["document_id"] == document_id and MARKER in row["text"] for row in search["items"])

        chunk = platform.call("GET", f"/versions/{version_id}/chunks?limit=1")["items"][0]
        original_boost = chunk["boost"]
        changed_boost = 1.4 if original_boost != 1.4 else 1.5
        decision = platform.call("POST", "/curation/decisions", json={
            "space_id": space["id"],
            "target_type": "chunk",
            "target_id": chunk["chunk_id"],
            "version_id": version_id,
            "field_path": "boost",
            "operation": "override",
            "value": changed_boost,
            "scope": "version_only",
            "reason_code": "group_lifecycle_acceptance",
            "reason_note": "验证人工治理会发布到当前搜索投影，并可完整回滚。",
            "auto_publish": True,
        })
        batch_id = decision["batch"]["id"]
        platform.wait_job(decision["job"]["id"])
        projected = platform.call("GET", f"/versions/{version_id}/chunks?limit=1")["items"][0]
        assert projected["boost"] == changed_boost

        rollback = platform.call("POST", f"/curation/batches/{batch_id}/rollback")
        batch_id = None
        platform.wait_job(rollback["job"]["id"])
        restored = platform.call("GET", f"/versions/{version_id}/chunks?limit=1")["items"][0]
        assert restored["boost"] == original_boost

        platform.call("DELETE", f"/documents/{document_id}")
        deleted = True
        after_delete = platform.call("POST", "/search", json={
            "query": MARKER,
            "space_ids": [space["id"]],
            "top_k": 10,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "filters": {},
        })
        assert all(row["document_id"] != document_id for row in after_delete["items"])

        # The old policy version stays traceable but can never be returned as
        # the current searchable version of that document.
        policy = next(row for row in platform.call("GET", "/documents") if row["title"] == "国联集团知识管理办法V1.0.docx")
        policy_detail = platform.call("GET", f"/documents/{policy['id']}")
        assert len(policy_detail["versions"]) >= 2
        policy_search = platform.call("POST", "/search", json={
            "query": "集团制度复核周期和废止版本",
            "space_ids": [spaces["gl-policy-acceptance"]["id"]],
            "top_k": 10,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "filters": {},
        })
        assert all(
            row["version_id"] == policy_detail["current_version_id"]
            for row in policy_search["items"]
            if row["document_id"] == policy["id"]
        )
        print(
            "group lifecycle passed: current-version filter, curation publish/rollback, "
            "and document deletion propagation"
        )
        return 0
    finally:
        if batch_id:
            rollback = platform.call("POST", f"/curation/batches/{batch_id}/rollback")
            if rollback.get("job"):
                platform.wait_job(rollback["job"]["id"])
        if not deleted:
            try:
                platform.call("DELETE", f"/documents/{document_id}")
            except RuntimeError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
