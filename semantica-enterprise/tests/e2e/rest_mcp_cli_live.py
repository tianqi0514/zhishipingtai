#!/usr/bin/env python3
"""End-to-end REST, MCP and CLI checks against the running Docker stack."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


API = os.getenv("TEST_API", "http://api:8080/api/v1").rstrip("/")
MCP_URL = os.getenv("TEST_MCP", "http://mcp-server:8091/mcp")
SPACE_CODE = os.getenv("TEST_SPACE_CODE", "m10-acceptance")
SEARCH_QUERY = os.getenv("TEST_SEARCH_QUERY", "该产品的主要定位")
GRAPH_QUERY = os.getenv("TEST_GRAPH_QUERY", "传神智库")
DOCUMENT_TITLE = os.getenv("TEST_DOCUMENT_TITLE", "fact.docx")
MAPPING_VERSION_ID = os.getenv("TEST_MAPPING_VERSION_ID", "")


def mcp_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    detail = " | ".join(str(getattr(item, "text", ""))[:300] for item in getattr(result, "content", []) or [])
    raise AssertionError(f"MCP 工具未返回结构化对象：{detail}")


async def run_mcp(token: str, space_id: str, chunk_id: str, document_id: str) -> dict[str, Any]:
    timeout = httpx.Timeout(30, read=600)
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as mcp_http:
      async with streamable_http_client(MCP_URL, http_client=mcp_http) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {item.name for item in (await session.list_tools()).tools}
            required = {
                "knowledge_search", "knowledge_chat", "knowledge_get_fragment",
                "knowledge_graph_query", "knowledge_get_document_profile",
            }
            if MAPPING_VERSION_ID:
                required.add("structured_query")
            assert required <= tools
            search = mcp_payload(await session.call_tool("knowledge_search", {
                "query": SEARCH_QUERY,
                "space_ids": [space_id],
                "top_k": 5,
            }))
            fragment = mcp_payload(await session.call_tool("knowledge_get_fragment", {"chunk_id": chunk_id}))
            graph = mcp_payload(await session.call_tool("knowledge_graph_query", {
                "space_ids": [space_id], "entity_query": GRAPH_QUERY, "limit": 10,
            }))
            profile = mcp_payload(await session.call_tool("knowledge_get_document_profile", {"document_id": document_id}))
            chat = mcp_payload(await session.call_tool("knowledge_chat", {
                "message": "该产品的主要定位是什么？请引用依据。",
                "space_ids": [space_id],
            }))
            structured = None
            if MAPPING_VERSION_ID:
                structured = mcp_payload(await session.call_tool("structured_query", {
                    "mapping_version_id": MAPPING_VERSION_ID,
                    "question": "2026 年已完成订单销售总额是多少？",
                    "max_rows": 20,
                }))
    assert search.get("items") and fragment.get("has_access") is True
    assert graph.get("entities") and profile.get("summary")
    assert chat.get("status") == "completed" and chat.get("answer") and chat.get("citations")
    if structured is not None:
        assert structured["result"]["status"] == "succeeded"
        assert str(structured["result"]["rows"][0]["total_sales"]) == "910000.00"
        assert structured["result"]["source_citations"]
    return {
        "tools": len(tools),
        "conversation_id": chat["conversation_id"],
        "events": len(chat.get("event_types") or []),
        "structured_query": "passed" if structured is not None else "not_requested",
    }


def main() -> None:
    password = os.getenv("TEST_ADMIN_PASSWORD") or os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
    with httpx.Client(base_url=API, timeout=600) as client:
        login = client.post("/auth/login", json={"username": "admin", "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        spaces = client.get("/spaces").json()
        space = next(item for item in spaces if item["code"] == SPACE_CODE)
        search = client.post("/search", json={"query": SEARCH_QUERY, "space_ids": [space["id"]], "top_k": 5})
        search.raise_for_status()
        first = search.json()["items"][0]
        fragment = client.get(f"/fragments/{first['chunk_id']}")
        fragment.raise_for_status()
        documents = client.get("/documents", params={"space_id": space["id"]}).json()
        profiled_document = next(item for item in documents if item["title"] == DOCUMENT_TITLE)
        mcp_result = asyncio.run(run_mcp(token, space["id"], first["chunk_id"], profiled_document["id"]))

        environment = {
            **os.environ,
            "CHUANSHEN_TOKEN": token,
            "CHUANSHEN_API_URL": API,
        }
        cli_search = subprocess.run(
            ["chuanshen", "search", SEARCH_QUERY, "--space", space["id"], "--top-k", "3"],
            capture_output=True,
            text=True,
            timeout=90,
            env=environment,
            check=True,
        )
        assert json.loads(cli_search.stdout).get("items")
        cli_fragment = subprocess.run(
            ["chuanshen", "fragment", first["chunk_id"]],
            capture_output=True,
            text=True,
            timeout=90,
            env=environment,
            check=True,
        )
        assert json.loads(cli_fragment.stdout).get("has_access") is True
        cli_chat = subprocess.run(
            ["chuanshen", "chat", "该产品支持哪些数据源？", "--space", space["id"]],
            capture_output=True,
            text=True,
            timeout=600,
            env=environment,
            check=True,
        )
        conversation_line = next(line for line in reversed(cli_chat.stdout.splitlines()) if line.startswith("conversation_id="))
        cli_conversation = conversation_line.split("=", 1)[1]
        cli_structured = "not_requested"
        if MAPPING_VERSION_ID:
            structured_process = subprocess.run(
                [
                    "chuanshen", "structured-query", "2026 年已完成订单销售总额是多少？",
                    "--mapping-version", MAPPING_VERSION_ID, "--max-rows", "20",
                ],
                capture_output=True,
                text=True,
                timeout=600,
                env=environment,
                check=True,
            )
            structured_result = json.loads(structured_process.stdout)
            assert str(structured_result["result"]["rows"][0]["total_sales"]) == "910000.00"
            cli_structured = "passed"
        client.delete(f"/conversations/{mcp_result['conversation_id']}").raise_for_status()
        client.delete(f"/conversations/{cli_conversation}").raise_for_status()

    print(json.dumps({
        "rest_search_items": len(search.json()["items"]),
        "mcp_tools": mcp_result["tools"],
        "mcp_events": mcp_result["events"],
        "cli_search": "passed",
        "cli_fragment": "passed",
        "cli_chat": "passed",
        "mcp_structured_query": mcp_result["structured_query"],
        "cli_structured_query": cli_structured,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
