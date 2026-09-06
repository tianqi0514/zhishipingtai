#!/usr/bin/env python3
"""Real REST/MCP/CLI smoke test for the isolated Guolian demo space.

This script never prints credentials or response bodies.  It expects an
already prepared demo space and removes only conversations that it creates.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


API = os.getenv("TEST_API", "http://api:8080/api/v1").rstrip("/")
MCP_URL = os.getenv("TEST_MCP", "http://mcp-server:8091/mcp")
SPACE_CODE = os.getenv("TEST_SPACE_CODE", "guolian-enterprise-demo")
USERNAME = os.getenv("TEST_USERNAME", "guolian_demo_admin")
PASSWORD_FILE = Path(os.getenv("TEST_PASSWORD_FILE", "/run/secrets/demo_password"))
VALID_AMOUNT_QUESTION = "按照现行制度统计口径，2026 年集团演示有效采购总额是多少？"
EXPECTED_VALID_AMOUNT = 1_700_000


def _mcp_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AssertionError("MCP 工具没有返回结构化对象")


def _single_numeric_result(payload: dict[str, Any]) -> float:
    rows = payload.get("rows") or (payload.get("result") or {}).get("rows") or []
    assert len(rows) == 1, rows
    numeric = []
    for value in rows[0].values():
        if isinstance(value, bool) or value is None:
            continue
        try:
            numeric.append(float(str(value).replace(",", "")))
        except ValueError:
            continue
    assert len(numeric) == 1, rows
    return numeric[0]


def _metric_query_contract(mapping: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    version = mapping["active_version"]
    manifest = version["manifest"]
    entity = next(item for item in manifest["entities"] if "供应商" in str(item.get("label") or ""))
    attributes = [item for item in manifest["attributes"] if item.get("entity_id") == entity["id"]]
    name_attribute = next(
        (item for item in attributes if "名称" in str(item.get("label") or "")),
        attributes[0],
    )
    plan = {
        "version": "chuanshen.semantic-query-plan/v1",
        "original_question": "演示数据库中一共有多少家供应商？",
        "intent": "统计供应商数量",
        "entity_ids": [entity["id"]],
        "outputs": [{
            "position": 1,
            "label": "供应商数量",
            "kind": "metric",
            "attribute_ids": [name_attribute["id"]],
            "aggregate": "count",
        }],
        "expected_cardinality": "single_value",
    }
    query_ir = {
        "version": "chuanshen.query-ir/v1",
        "from_entity": {"binding": "s", "entity_id": entity["id"]},
        "select": [{
            "expression": {
                "kind": "aggregate",
                "function": "count",
                "distinct": True,
                "expression": {
                    "kind": "attribute",
                    "attribute_id": name_attribute["id"],
                    "binding": "s",
                },
            },
            "alias": "supplier_count",
        }],
    }
    return plan, query_ir, version["id"]


async def _run_mcp(
    token: str,
    space_id: str,
    chunk_id: str,
    document_id: str,
    plan: dict[str, Any],
    query_ir: dict[str, Any],
    mapping_version_id: str,
) -> tuple[dict[str, Any], str]:
    print(json.dumps({"stage": "mcp_connect"}, ensure_ascii=False), flush=True)
    timeout = httpx.Timeout(30, read=600)
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as client:
        async with streamable_http_client(MCP_URL, http_client=client) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {item.name for item in (await session.list_tools()).tools}
                required = {
                    "knowledge_search", "knowledge_chat", "knowledge_get_fragment",
                    "knowledge_graph_query", "knowledge_get_document_profile",
                    "structured_schema_search", "structured_get_object", "structured_execute_query",
                }
                assert required <= tools, sorted(required - tools)
                print(json.dumps({"stage": "mcp_tools", "count": len(tools)}, ensure_ascii=False), flush=True)
                search = _mcp_payload(await session.call_tool("knowledge_search", {
                    "query": "采购实施细则",
                    "space_ids": [space_id],
                    "top_k": 3,
                    "use_keyword": True,
                    "use_vector": False,
                    "use_graph": False,
                    "use_reranker": False,
                }))
                fragment = _mcp_payload(await session.call_tool("knowledge_get_fragment", {"chunk_id": chunk_id}))
                graph = _mcp_payload(await session.call_tool("knowledge_graph_query", {
                    "space_ids": [space_id], "entity_query": "NexusOne", "limit": 10,
                }))
                profile = _mcp_payload(await session.call_tool(
                    "knowledge_get_document_profile", {"document_id": document_id}
                ))
                catalog = _mcp_payload(await session.call_tool("structured_schema_search", {
                    "query": "供应商", "space_ids": [space_id], "limit": 10,
                }))
                semantic_object = catalog["semantic_objects"][0]
                detail = _mcp_payload(await session.call_tool("structured_get_object", {
                    "semantic_object_id": semantic_object["semantic_object_id"],
                    "mapping_version_id": semantic_object["mapping_version_id"],
                }))
                structured = _mcp_payload(await session.call_tool("structured_execute_query", {
                    "semantic_query_plan": plan,
                    "query_ir": query_ir,
                    "mapping_version_id": mapping_version_id,
                    "max_rows": 20,
                }))
                print(json.dumps({"stage": "mcp_structured", "row_count": structured.get("row_count")}, ensure_ascii=False), flush=True)
                raw_sql_rejected = False
                try:
                    invalid = await session.call_tool("structured_execute_query", {
                        "semantic_query_plan": plan,
                        "query_ir": query_ir,
                        "mapping_version_id": mapping_version_id,
                        "max_rows": 20,
                        "sql": "DROP TABLE suppliers",
                    })
                    raw_sql_rejected = bool(
                        getattr(invalid, "isError", False) or getattr(invalid, "is_error", False)
                    )
                except Exception:
                    raw_sql_rejected = True
                chat = _mcp_payload(await session.call_tool("knowledge_chat", {
                    "message": VALID_AMOUNT_QUESTION,
                    "space_ids": [space_id],
                }))
                print(json.dumps({"stage": "mcp_chat", "status": chat.get("status")}, ensure_ascii=False), flush=True)
    assert search.get("items")
    assert fragment.get("has_access") is True
    assert graph.get("entities")
    assert profile.get("summary")
    assert detail.get("attributes")
    assert structured.get("status") == "succeeded"
    assert _single_numeric_result(structured) == EXPECTED_VALID_AMOUNT
    assert structured.get("source_citations")
    assert raw_sql_rejected
    assert chat.get("status") == "completed"
    compact_answer = re.sub(r"[\s,，]", "", str(chat.get("answer") or ""))
    assert "1700000" in compact_answer or "170万" in compact_answer
    assert chat.get("citations")
    return {
        "tool_count": len(tools),
        "search_items": len(search["items"]),
        "graph_entities": len(graph["entities"]),
        "structured_rows": structured["rows"],
        "chat_events": len(chat.get("event_types") or []),
        "chat_citations": len(chat.get("citations") or []),
        "raw_sql_rejected": raw_sql_rejected,
    }, chat["conversation_id"]


def main() -> None:
    print(json.dumps({"stage": "rest_login"}, ensure_ascii=False), flush=True)
    password = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    with httpx.Client(base_url=API, timeout=600) as client:
        login = client.post("/auth/login", json={"username": USERNAME, "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        space = next(item for item in client.get("/spaces").json() if item["code"] == SPACE_CODE)
        search = client.post("/search", json={
            "query": "采购实施细则", "space_ids": [space["id"]], "top_k": 3,
            "use_keyword": True, "use_vector": False, "use_graph": False, "use_reranker": False,
        })
        search.raise_for_status()
        first = search.json()["items"][0]
        documents = client.get("/documents", params={"space_id": space["id"]}).json()
        document = next(item for item in documents if "采购实施细则（2025" in item["title"])
        mappings = client.get("/semantic-mappings", params={"space_id": space["id"]}).json()["items"]
        mapping = next(item for item in mappings if item.get("active_version"))
        mapping_version_id = mapping["active_version"]["id"]
        rest_structured = client.post("/structured-query/natural-language", json={
            "mapping_version_id": mapping_version_id,
            "question": VALID_AMOUNT_QUESTION,
            "execute": True,
            "max_rows": 20,
        })
        rest_structured.raise_for_status()
        rest_structured_payload = rest_structured.json()
        assert _single_numeric_result(rest_structured_payload["result"]) == EXPECTED_VALID_AMOUNT
        assert "EXISTS" in rest_structured_payload["compiled"].get("sql_template", "").upper()
        assert rest_structured_payload["result"]["source_citations"]
        plan = rest_structured_payload["plan"]
        query_ir = rest_structured_payload["query_ir"]
        print(json.dumps({
            "stage": "rest_structured",
            "rows": rest_structured_payload["result"]["rows"],
            "citations": len(rest_structured_payload["result"]["source_citations"]),
        }, ensure_ascii=False), flush=True)

        mcp_summary, mcp_conversation_id = asyncio.run(_run_mcp(
            token, space["id"], first["chunk_id"], document["id"],
            plan, query_ir, mapping_version_id,
        ))
        print(json.dumps({"stage": "mcp", **mcp_summary}, ensure_ascii=False), flush=True)

        environment = {
            **os.environ,
            "CHUANSHEN_TOKEN": token,
            "CHUANSHEN_API_URL": API,
            "CHUANSHEN_CONFIG": "/tmp/chuanshen-demo-live.json",
        }

        def cli(*arguments: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", "apps.cli", *arguments],
                capture_output=True, text=True, timeout=timeout, check=False, env=environment,
            )

        spaces_result = cli("spaces")
        spaces_payload = json.loads(spaces_result.stdout) if spaces_result.returncode == 0 else {}
        search_result = cli("search", "采购实施细则", "--space", space["id"], "--top-k", "2")
        search_payload = json.loads(search_result.stdout) if search_result.returncode == 0 else {}
        fragment_result = cli("fragment", first["chunk_id"])
        fragment_payload = json.loads(fragment_result.stdout) if fragment_result.returncode == 0 else {}
        structured_result = cli(
            "structured-query", VALID_AMOUNT_QUESTION,
            "--mapping-version", mapping_version_id, "--max-rows", "20",
        )
        structured_payload = json.loads(structured_result.stdout) if structured_result.returncode == 0 else {}
        chat_result = cli(
            "chat", "采购实施细则的适用范围是什么？请引用依据。",
            "--space", space["id"],
        )
        match = re.search(r"conversation_id=([^\s]+)", chat_result.stdout)
        cli_conversation_id = match.group(1) if match else ""
        assert spaces_result.returncode == 0
        assert any(item.get("code") == SPACE_CODE for item in spaces_payload.get("items") or [])
        assert search_result.returncode == 0 and search_payload.get("items")
        assert fragment_result.returncode == 0 and fragment_payload.get("has_access") is True
        assert structured_result.returncode == 0
        assert _single_numeric_result(structured_payload["result"]) == EXPECTED_VALID_AMOUNT
        assert chat_result.returncode == 0 and cli_conversation_id
        assert token not in "".join([
            spaces_result.stdout, spaces_result.stderr,
            search_result.stdout, search_result.stderr,
            fragment_result.stdout, fragment_result.stderr,
            structured_result.stdout, structured_result.stderr,
            chat_result.stdout, chat_result.stderr,
        ])
        print(json.dumps({
            "stage": "cli",
            "spaces": "passed",
            "search_items": len(search_payload["items"]),
            "fragment": "passed",
            "structured_rows": structured_payload["result"]["rows"],
            "chat": "passed",
            "secret_safe": True,
        }, ensure_ascii=False), flush=True)
        for conversation_id in (mcp_conversation_id, cli_conversation_id):
            if conversation_id:
                client.delete(f"/conversations/{conversation_id}").raise_for_status()


if __name__ == "__main__":
    main()
