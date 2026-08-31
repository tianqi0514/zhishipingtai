#!/usr/bin/env python3
"""Protocol-compatible Google Drive, Microsoft Graph and search fixtures."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


FACT = b"NexusOne supports cloud knowledge ingestion and hybrid retrieval.\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def send(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Elastic-Product", "Elasticsearch")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def json(self, payload: dict, status: int = 200) -> None:
        self.send(status, json.dumps(payload).encode("utf-8"))

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        self.send(200 if path in {"/", "/knowledge"} else 404, b"")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if path == "/oauth/token":
            fields = parse_qs(body.decode("utf-8"))
            assert fields.get("grant_type") == ["refresh_token"]
            self.json({"access_token": "fixture-access-token", "expires_in": 3600, "token_type": "Bearer"})
        elif path == "/knowledge/_count":
            self.json({"count": 1, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}})
        elif path in {"/opensearch/knowledge/_search", "/knowledge/_search"}:
            self.json({
                "took": 1,
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [{"_id": "1", "_index": "knowledge", "_score": 1.0, "_source": {"fact": FACT.decode().strip()}}],
                },
            })
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        assert self.headers.get("Authorization") == "Bearer fixture-access-token" or path.startswith(("/downloads/", "/knowledge", "/"))
        if path == "/":
            self.json({
                "name": "cloud-fixture",
                "cluster_name": "fixture",
                "version": {"number": "8.17.0", "build_flavor": "default", "build_type": "docker"},
                "tagline": "You Know, for Search",
            })
        elif path == "/drive/v3/files" and "q" in query:
            folder = query["q"][0].split("'", 2)[1]
            if folder == "root":
                self.json({
                    "files": [
                        {"id": "g-folder", "name": "nested", "mimeType": "application/vnd.google-apps.folder"},
                        {"id": "g-file", "name": "drive-fact.txt", "mimeType": "text/plain", "size": str(len(FACT))},
                    ]
                })
            else:
                self.json({"files": [{"id": "g-native", "name": "manual", "mimeType": "application/vnd.google-apps.document"}]})
        elif path == "/drive/v3/files/g-file" and query.get("alt") == ["media"]:
            self.send(200, FACT, "text/plain")
        elif path == "/drive/v3/files/g-native/export":
            self.send(200, FACT, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        elif path == "/graph/sites/site1/drive":
            self.json({"id": "drive1"})
        elif path in {"/graph/me/drive/root/children", "/graph/drives/drive1/root/children"}:
            drive = "me" if "/me/" in path else "drive1"
            self.json({
                "value": [
                    {"id": f"{drive}-folder", "name": "nested", "folder": {"childCount": 1}, "parentReference": {"driveId": drive}},
                    {
                        "id": f"{drive}-file", "name": f"{drive}-fact.txt", "file": {"mimeType": "text/plain"},
                        "@microsoft.graph.downloadUrl": f"http://cloud-fixture:8096/downloads/{drive}-fact.txt",
                    },
                ]
            })
        elif path in {"/graph/drives/me/items/me-folder/children", "/graph/drives/drive1/items/drive1-folder/children"}:
            drive = "me" if "/drives/me/" in path else "drive1"
            self.json({
                "value": [{
                    "id": f"{drive}-nested", "name": "nested-fact.txt", "file": {"mimeType": "text/plain"},
                    "@microsoft.graph.downloadUrl": f"http://cloud-fixture:8096/downloads/{drive}-nested.txt",
                }]
            })
        elif path.startswith("/downloads/"):
            self.send(200, FACT, "text/plain")
        elif path == "/knowledge/_count":
            self.json({"count": 1, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}})
        else:
            self.send_error(404)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8096), Handler).serve_forever()
