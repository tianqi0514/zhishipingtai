#!/usr/bin/env python3
"""Read-only MCP transport/source probe. Never prints or persists credentials.

Does not register connectors, mutate services, issue Agent messages, or change
business records. It logs in, discovers tools, reads one known source fragment,
and verifies missing credentials cannot read that source.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib import request, error


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--credentials", type=Path, required=True)
    p.add_argument("--api", default="http://127.0.0.1:9002/api/v1")
    p.add_argument("--mcp", default="http://127.0.0.1:18091/mcp")
    p.add_argument("--fragment", required=True)
    p.add_argument("--diagnostic-host", default="127.0.0.1:8091")
    args = p.parse_args()
    if args.credentials.stat().st_mode & 0o077:
        raise SystemExit("Unsafe credential file permissions")

    def call(url, body=None, headers=None):
        req = request.Request(url, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})})
        try:
            with request.urlopen(req, timeout=30) as response:
                status, raw = response.status, response.read()
        except error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"non_json_bytes": len(raw)}

    credential = json.loads(args.credentials.read_text())
    status, login = call(args.api + "/auth/login", {"username": credential["username"], "password": credential["password"]})
    if status != 200 or not login.get("access_token"):
        raise SystemExit("Platform login failed; no credential details emitted")
    authorization = {"Authorization": "Bearer " + login["access_token"]}
    _, actor = call(args.api + "/auth/me", headers=authorization)
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "readonly-source-diagnostic", "version": "1"}}}
    actual_status, actual = call(args.mcp, initialize, authorization)
    corrected_headers = {**authorization, "Host": args.diagnostic_host}
    diagnostic_status, initialized = call(args.mcp, initialize, corrected_headers)
    # Only the diagnostic initialization overrides Host. All source/permission
    # checks use the exact URL/headers available to the real DSH connector.
    tool_status, tools = call(args.mcp, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, authorization)
    arguments = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "knowledge_get_fragment", "arguments": {"chunk_id": args.fragment}}}
    read_status, read = call(args.mcp, arguments, authorization)
    anonymous_status, anonymous = call(args.mcp, arguments)
    invalid_status, invalid = call(args.mcp, arguments, {"Authorization": "Bearer invalid-readonly-probe"})
    unexpected_host_status, _ = call(args.mcp, initialize, {**authorization, "Host": "untrusted.invalid:18091"})
    content = (read.get("result") or {}).get("content") or []
    source_text = "\n".join(str(item.get("text") or "") for item in content)
    print(json.dumps({"configured_endpoint_status": actual_status,
        "diagnostic_host_only_status": diagnostic_status,
        "initialized": bool((initialized.get("result") or {}).get("serverInfo")),
        "tools_status": tool_status, "tool_names": [row["name"] for row in (tools.get("result") or {}).get("tools") or []],
        "source_read_status": read_status, "source_is_error": (read.get("result") or {}).get("isError", False),
        "source_response_chars": len(source_text), "source_response_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "source_chunk_id": args.fragment, "anonymous_status": anonymous_status,
        "anonymous_denied": bool((anonymous.get("result") or {}).get("isError") or anonymous.get("error")),
        "invalid_credential_status": invalid_status,
        "invalid_credential_denied": bool((invalid.get("result") or {}).get("isError") or invalid.get("error")),
        "unexpected_host_status": unexpected_host_status,
        "probe_account_is_admin": bool(actor.get("is_admin")),
        "readonly_principal_verified": False, "connector_registered": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
