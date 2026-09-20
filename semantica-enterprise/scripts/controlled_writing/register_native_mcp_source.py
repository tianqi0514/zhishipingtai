#!/usr/bin/env python3
"""Provision a space-restricted MCP identity and official DSH connector.

Run only on the deployment server. Secrets are read from owner-only files,
passed directly between authenticated APIs, and never emitted in traces.
No database writes or plugin configuration-file edits are performed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib import error, parse, request

def secure_json(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, ensure_ascii=False)


def read_secret(path: Path):
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Credential permissions are not owner-only")
    return json.loads(path.read_text())


def call(url, body=None, *, token=None, method=None, opener=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=None if body is None else json.dumps(body).encode(),
                          headers=headers, method=method)
    try:
        with (opener.open(req, timeout=45) if opener else request.urlopen(req, timeout=45)) as response:
            status, raw = response.status, response.read()
    except error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, {"non_json_bytes": len(raw)}


def require(condition, message):
    if not condition:
        # Never embed arbitrary upstream responses: they may contain secrets.
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--dsh-service", default="workdsh-preview.service")
    parser.add_argument("--reader-credentials", type=Path, required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--fragment", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:9002/api/v1")
    parser.add_argument("--dsh", default="http://127.0.0.1:18991")
    parser.add_argument("--mcp", default="http://127.0.0.1:18091/mcp")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    admin_credential = read_secret(args.credentials)
    status, result = call(args.api + "/auth/login", {
        "username": admin_credential["username"], "password": admin_credential["password"]})
    require(status == 200 and result.get("access_token"), "Admin login failed")
    admin_token = result["access_token"]
    status, space = call(args.api + "/spaces/" + args.space, token=admin_token)
    require(status == 200 and space.get("id") == args.space, "Exact test space not found")

    if args.reader_credentials.exists():
        reader = read_secret(args.reader_credentials)
        require(reader.get("space_id") == args.space, "Existing reader belongs to another space")
    else:
        reader = {"username": "native_mcp_" + args.space.replace("-", "")[:12],
                  "password": secrets.token_urlsafe(36), "space_id": args.space}
        secure_json(args.reader_credentials, reader)
    status, users = call(args.api + "/users", token=admin_token)
    require(status == 200 and isinstance(users, list), "Cannot inspect users")
    found = [item for item in users if item.get("username") == reader["username"]]
    require(len(found) <= 1, "Ambiguous reader identity")
    if found:
        user = found[0]
    else:
        status, user = call(args.api + "/users", {
            "username": reader["username"], "password": reader["password"],
            "display_name": "科研楼验收 MCP 资料身份", "is_admin": False,
            "enabled": True, "role_ids": [], "org_unit_id": None}, token=admin_token)
        require(status == 200 and user.get("id"), "Cannot create restricted reader")
    require(not user.get("is_admin") and not user.get("org_unit_id") and not user.get("role_ids"),
            "Reader has unexpected global authority")
    reader["user_id"] = user["id"]
    secure_json(args.reader_credentials, reader)

    status, grant = call(args.api + f"/spaces/{args.space}/grants", {
        "subject_type": "user", "subject_id": user["id"], "permission": "read", "effect": "allow"}, token=admin_token)
    require(status == 200 and grant.get("permission") == "read", "Cannot grant test-space read")
    status, result = call(args.api + "/auth/login", {
        "username": reader["username"], "password": reader["password"]})
    require(status == 200 and result.get("access_token"), "Reader login failed")
    reader_token = result["access_token"]
    status, actor = call(args.api + "/auth/me", token=reader_token)
    require(status == 200 and not actor.get("is_admin"), "Reader was unexpectedly elevated")
    status, visible = call(args.api + "/spaces", token=reader_token)
    require(status == 200 and {row["id"] for row in visible} == {args.space}, "Reader has unintended space access")
    _, spaces = call(args.api + "/spaces", token=admin_token)
    other = next((row["id"] for row in spaces if row["id"] != args.space), None)
    require(other, "No separate space available for denial test")
    cross_status, _ = call(args.api + "/spaces/" + other, token=reader_token)
    require(cross_status == 403, "Cross-space read was not denied")
    status, fragment = call(args.api + "/fragments/" + args.fragment, token=reader_token)
    require(status == 200, "Reader cannot read genuine source fragment")
    document_id = fragment.get("document_id") or (fragment.get("document") or {}).get("id")
    require(document_id, "Source fragment lacks owning document")
    # Empty update is still an authorized write operation. Even if a permission
    # regression occurs, this probe never changes a customer document field.
    write_status, _ = call(args.api + "/documents/" + document_id, {}, token=reader_token, method="PUT")
    require(write_status == 403, "Read-scoped identity can write source document")
    mcp_request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "knowledge_get_fragment", "arguments": {"chunk_id": args.fragment}}}
    mcp_status, mcp_result = call(args.mcp, mcp_request, token=reader_token)
    require(mcp_status == 200 and mcp_result.get("result") and not mcp_result["result"].get("isError"),
            "Restricted MCP source read failed")
    anonymous_status, anonymous = call(args.mcp, mcp_request)
    require(anonymous_status == 200 and (anonymous.get("result") or {}).get("isError"), "Anonymous MCP source read was not denied")

    # DSH's credential record is a signing key, NOT a bearer API credential.
    # Use the official launch URL once to establish an authority-bound cookie.
    # Journal output and the launch token remain only in this process memory.
    journal = subprocess.check_output(["journalctl", "-u", args.dsh_service,
        "-n", "500", "--no-pager", "-o", "cat"], text=True)
    launch_tokens = re.findall(r"[?&]token=([A-Za-z0-9._~%+-]+)", journal)
    require(launch_tokens, "No current DSH launch URL found in service journal")
    dsh_opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))
    launch_token = parse.unquote(launch_tokens[-1])
    launch_request = request.Request(args.dsh.rstrip("/") + "/?" + parse.urlencode({"token": launch_token}))
    with dsh_opener.open(launch_request, timeout=30) as response:
        require(response.status == 200, "DSH launch handshake failed")

    def connector(endpoint, payload):
        status, response = call(args.dsh + "/api/workdsh-connectors", {"endpoint": endpoint, "payload": payload}, opener=dsh_opener)
        require(status == 200 and response.get("ok"), "Official connector management request failed: " + endpoint)
        return response["value"]

    rows = connector("list", {})
    existing = next((row for row in rows if row.get("serverName") == "chuanshen_research"), None)
    config = {"title": "科研楼资料 · 空间受限连接器", "description":
        "仅授权科研楼验收空间资料读取。包含检索、原文及分析工具；会话和推演可能创建运行记录。",
        "serverName": "chuanshen_research", "transport": "streamable-http", "url": args.mcp,
        "credentialHeader": "Authorization", "authorizationToken": "Bearer " + reader_token}
    registered = connector("update", {"id": existing["id"], "input": config}) if existing else connector("create", config)
    connector_id = registered["id"]
    for _ in range(10):
        catalog = connector("list", {})
        current = next(row for row in catalog if row["id"] == connector_id)
        if current.get("state") == "ready":
            break
        time.sleep(1)
    require(current.get("state") == "ready", "Registered connector did not discover tools")
    require(any(name.endswith("knowledge_get_fragment") for name in current.get("toolNames", [])),
            "Registered MCP has no source fragment tool")
    safe_config = connector("config", {"id": connector_id})
    require(safe_config.get("authorizationConfigured") and "authorizationToken" not in safe_config,
            "Connector config must expose configured state only")

    # Decode expiry solely for reporting. Platform already verified the token;
    # this does not authenticate or re-sign it and never reports token bytes.
    jwt_payload = json.loads(base64.urlsafe_b64decode(reader_token.split(".")[1] + "=="))
    content = json.dumps(mcp_result["result"], ensure_ascii=False, sort_keys=True)
    report = {"space_id": args.space, "reader_user_id": user["id"], "is_admin": False,
        "visible_space_count": len(visible), "cross_space_status": cross_status,
        "source_write_status": write_status, "source_chunk_id": args.fragment,
        "mcp_read_status": mcp_status, "mcp_source_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "anonymous_denied": True, "connector_id": connector_id, "connector_state": current["state"],
        "tool_names": current.get("toolNames"), "authorization_configured": True,
        "token_expires_at": jwt_payload["exp"], "project_selection_available": True,
        "selected_in_session": False, "strict_tool_readonly": False}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Our own errors only; do not expose HTTP response bodies or secrets.
        if isinstance(exc, RuntimeError):
            raise SystemExit(str(exc))
        raise SystemExit("Registration failed: " + type(exc).__name__)
