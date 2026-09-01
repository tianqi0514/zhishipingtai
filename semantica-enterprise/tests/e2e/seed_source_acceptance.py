#!/usr/bin/env python3
"""Create persistent source connectors and exercise their real API lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
LOCAL_ROOT = "/app/data/sources/acceptance-local"


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        login = self.call("POST", "/auth/login", json={"username": USERNAME, "password": PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1500]}")
        return response.json() if response.content else {}

    def wait_job(self, job_id: str, timeout: int = 1800) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.call("GET", f"/jobs/{job_id}")
            if last["status"] in {"succeeded", "failed"}:
                if last["status"] != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:4000])
                return last
            time.sleep(2)
        raise TimeoutError(f"job {job_id}: {last}")

    def wait_process(self, version_id: str, timeout: int = 1800) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = next(
                (
                    row for row in self.call("GET", "/jobs")
                    if row.get("job_type") == "process_knowledge"
                    and (row.get("input") or {}).get("version_id") == version_id
                ),
                None,
            )
            if job:
                return self.wait_job(job["id"], max(1, int(deadline - time.monotonic())))
            time.sleep(2)
        raise TimeoutError(f"process job for {version_id} was not created")


def source_definitions() -> list[dict[str, Any]]:
    return [
        {"name": "验收·企业网页", "source_type": "web", "config": {"url": "http://source-fixture:8088/page", "respect_robots": True}},
        {"name": "验收·REST API", "source_type": "rest", "config": {"url": "http://source-fixture:8088/api", "method": "GET"}},
        {"name": "验收·RSS 订阅", "source_type": "rss", "config": {"url": "http://source-fixture:8088/feed.xml", "max_items": 10}},
        {"name": "验收·Sitemap", "source_type": "sitemap", "config": {"url": "http://source-fixture:8088/sitemap.xml", "max_urls": 5, "respect_robots": True}},
        {"name": "验收·WebDAV", "source_type": "webdav", "config": {"url": "http://source-fixture:8088/dav/"}},
        {"name": "验收·挂载目录", "source_type": "local_dir", "config": {"path": LOCAL_ROOT, "recursive": True, "max_files": 10}},
        {"name": "验收·MinIO S3", "source_type": "s3", "config": {"endpoint": "minio:9000", "bucket": "acceptance-source", "prefix": "knowledge/", "access_key": "semantica", "secure": False}, "secret": "semantica-dev-secret"},
        {"name": "验收·PostgreSQL", "source_type": "database", "config": {"dialect": "postgresql", "host": "postgres", "port": 5432, "database": "semantica", "username": "semantica", "include_tables": ["schema_migrations"], "max_rows_per_table": 20}, "secret": "semantica"},
        {"name": "验收·MCP 资源", "source_type": "mcp", "config": {"url": "http://protocol-fixture:8095/mcp", "server_name": "acceptance", "timeout": 30}},
        {"name": "验收·SFTP", "source_type": "sftp", "config": {"host": "protocol-fixture", "port": 2222, "username": "fixture", "path": "/", "recursive": True}, "secret": "Fixture@123456"},
        {"name": "验收·FTP", "source_type": "ftp", "config": {"host": "protocol-fixture", "port": 2121, "username": "fixture", "path": "/", "recursive": True}, "secret": "Fixture@123456"},
        {"name": "验收·FTPS", "source_type": "ftps", "config": {"host": "protocol-fixture", "port": 2990, "username": "fixture", "path": "/", "recursive": True}, "secret": "Fixture@123456"},
        {"name": "验收·IMAP 邮箱", "source_type": "email", "config": {"protocol": "imap", "server": "protocol-fixture", "port": 1993, "username": "fixture", "mailbox": "INBOX", "max_emails": 5}, "secret": "Fixture@123456"},
    ]


def main() -> None:
    platform = Platform()
    space = next(row for row in platform.call("GET", "/spaces") if row["code"] == "acceptance-main")
    existing = {row["name"] for row in platform.call("GET", "/sources")}
    definitions = source_definitions()
    conflicts = existing & {row["name"] for row in definitions}
    if conflicts:
        raise RuntimeError(f"验收数据源已存在：{sorted(conflicts)}")

    created: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for definition in definitions:
        payload = {"space_id": space["id"], "enabled": True, **definition}
        source = platform.call("POST", "/sources", json=payload)
        tested = platform.call(
            "POST",
            "/sources/test",
            json={
                "source_id": source["id"],
                "source_type": source["source_type"],
                "config": source["config"],
            },
        )
        created.append((definition, source, tested))
        print(json.dumps({"source": source["name"], "connection": tested["status"], "bytes": tested["bytes"]}, ensure_ascii=False), flush=True)

    sync_types = {"web", "rss", "webdav", "local_dir", "s3", "mcp", "sftp", "email"}
    sync_jobs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for definition, source, _tested in created:
        if definition["source_type"] in sync_types:
            sync_jobs.append((source, platform.call("POST", f"/sources/{source['id']}/sync")))

    synced: list[dict[str, Any]] = []
    for source, queued in sync_jobs:
        sync = platform.wait_job(queued["id"])
        parse = platform.wait_job(sync["result"]["parse_job_id"])
        process = platform.wait_process(sync["result"]["version_id"])
        result = {
            "source": source["name"],
            "document_id": sync["result"]["document_id"],
            "version_id": sync["result"]["version_id"],
            "parse": parse["status"],
            "process": process["status"],
        }
        synced.append(result)
        print(json.dumps({"synced": result}, ensure_ascii=False), flush=True)

    local_source = next(source for definition, source, _ in created if definition["source_type"] == "local_dir")
    first_local = next(row for row in synced if row["source"] == local_source["name"])
    unchanged = platform.wait_job(platform.call("POST", f"/sources/{local_source['id']}/sync")["id"])
    assert unchanged["result"].get("unchanged") is True
    assert unchanged["result"]["version_id"] == first_local["version_id"]

    subprocess.run(
        ["python", str(ROOT / "tests/fixtures/update_source_volume.py"), LOCAL_ROOT, "phase2"],
        check=True,
    )
    changed = platform.wait_job(platform.call("POST", f"/sources/{local_source['id']}/sync")["id"])
    assert changed["result"]["version_id"] != first_local["version_id"]
    platform.wait_job(changed["result"]["parse_job_id"])
    changed_process = platform.wait_process(changed["result"]["version_id"])
    chunks = platform.call("GET", f"/versions/{changed['result']['version_id']}/chunks?limit=100")["items"]
    reused = [row for row in chunks if (row.get("source_span") or {}).get("incremental") == "unchanged"]
    modified = [row for row in chunks if (row.get("source_span") or {}).get("incremental") == "changed"]
    assert reused and modified

    print(json.dumps({
        "created": len(created),
        "connections_succeeded": len(created),
        "sources_synced": len(synced),
        "unchanged_sync": True,
        "incremental_version_created": True,
        "reused_chunks": len(reused),
        "changed_chunks": len(modified),
        "semantic_step": next(step["detail"] for step in changed_process["steps"] if step["name"] == "semantic_extract"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
