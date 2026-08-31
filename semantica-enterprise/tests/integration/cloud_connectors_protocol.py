#!/usr/bin/env python3
"""Official-protocol compatible cloud connector integration checks."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.semantica_adapter.ingest import ingest_source


TOKEN_SECRET = json.dumps({
    "refresh_token": "fixture-refresh",
    "client_id": "fixture-client",
    "client_secret": "fixture-secret",
    "token_uri": "http://cloud-fixture:8096/oauth/token",
})


def check_archive(name: str, source_type: str, config: dict[str, Any]) -> dict[str, Any]:
    result = ingest_source(source_type=source_type, source_name=name, config=config, secret=TOKEN_SECRET)
    with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
        bodies = [archive.read(member) for member in archive.namelist()]
    assert any(b"NexusOne" in body for body in bodies), (name, archive.namelist())
    return result.metadata


def main() -> None:
    oauth = {"token_url": "http://cloud-fixture:8096/oauth/token", "timeout": 10, "max_files": 10}
    results = {
        "google_drive": check_archive(
            "google-drive-fixture",
            "google_drive",
            {**oauth, "api_base_url": "http://cloud-fixture:8096/drive/v3", "folder_id": "root", "recursive": True},
        ),
        "onedrive": check_archive(
            "onedrive-fixture",
            "onedrive",
            {**oauth, "graph_base_url": "http://cloud-fixture:8096/graph", "drive_id": "me", "recursive": True},
        ),
        "sharepoint": check_archive(
            "sharepoint-fixture",
            "sharepoint",
            {**oauth, "graph_base_url": "http://cloud-fixture:8096/graph", "site_id": "site1", "recursive": True},
        ),
    }
    assert results["google_drive"]["file_count"] == 2
    assert results["onedrive"]["file_count"] == 2
    assert results["sharepoint"]["file_count"] == 2
    search = ingest_source(
        source_type="opensearch",
        source_name="opensearch-fixture",
        config={"url": "http://cloud-fixture:8096/opensearch", "index": "knowledge", "limit": 10},
        secret="fixture-access-token",
    )
    assert b"NexusOne" in search.body and search.metadata["document_count"] == 1
    results["opensearch"] = search.metadata
    elastic = ingest_source(
        source_type="elasticsearch",
        source_name="elasticsearch-fixture",
        config={"url": "http://cloud-fixture:8096", "index": "knowledge", "limit": 10, "verify_certs": False},
    )
    assert b"NexusOne" in elastic.body and elastic.metadata["document_count"] == 1
    results["elasticsearch"] = elastic.metadata
    print(json.dumps({"connectors": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
