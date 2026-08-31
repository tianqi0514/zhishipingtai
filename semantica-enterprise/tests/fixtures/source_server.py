#!/usr/bin/env python3
"""Small deterministic HTTP/RSS/Sitemap/WebDAV protocol fixture."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


FACT = "NexusOne fixture source supports enterprise knowledge ingestion."
REVISION = {"value": 1}
REVISION_LOCK = Lock()


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        base = "http://source-fixture:8088"
        if self.path == "/robots.txt":
            return self.respond(200, "text/plain", b"User-agent: *\nAllow: /\n")
        if self.path in {"/", "/page"}:
            return self.respond(200, "text/html; charset=utf-8", f"<title>NexusOne</title><p>{FACT}</p>".encode())
        if self.path == "/api":
            with REVISION_LOCK:
                revision = REVISION["value"]
            return self.respond(200, "application/json", json.dumps({"product": "NexusOne", "year": 2026, "revision": revision}).encode())
        if self.path == "/feed.xml":
            body = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>NexusOne Feed</title><link>{base}/</link><description>fixture</description><item><title>Release</title><link>{base}/page</link><description>{FACT}</description></item></channel></rss>"""
            return self.respond(200, "application/rss+xml", body.encode())
        if self.path == "/sitemap.xml":
            body = f"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{base}/page</loc></url></urlset>"""
            return self.respond(200, "application/xml", body.encode())
        if self.path == "/dav/fact.txt":
            return self.respond(200, "text/plain; charset=utf-8", FACT.encode())
        return self.respond(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/control/revision":
            return self.respond(404, "text/plain", b"not found")
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        with REVISION_LOCK:
            REVISION["value"] = int(payload.get("revision") or 1)
            revision = REVISION["value"]
        return self.respond(200, "application/json", json.dumps({"revision": revision}).encode())

    def do_PROPFIND(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/dav":
            return self.respond(404, "text/plain", b"not found")
        body = b"""<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href></d:response><d:response><d:href>/dav/fact.txt</d:href></d:response></d:multistatus>"""
        return self.respond(207, "application/xml", body)

    def log_message(self, *_args) -> None:
        return


ThreadingHTTPServer(("0.0.0.0", 8088), Handler).serve_forever()
