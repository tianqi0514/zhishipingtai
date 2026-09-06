#!/usr/bin/env python3
"""Verify the live REST, Harness, MCP and CLI demo service chain.

The verifier calls only deployed interfaces.  It never imports a database
session, search backend or Agent implementation, and it never prints response
bodies, credentials, model prompts or generated answers.  Conversations made
for verification are removed in ``finally`` while their immutable audit trail
remains governed by the platform.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (  # noqa: E402
    SPACE_CODE,
    admin_credentials_from_environment,
    api_url_from_environment,
)


CURRENT_POLICY_TITLE = "集团本部采购实施细则（2025演示现行版）.md"
EXPECTED_SUPPLIER_COUNT = 5
REQUIRED_DSH_EVENTS = frozenset({
    "turn_started",
    "step_started",
    "tool_started",
    "retrieval_started",
    "tool_finished",
    "retrieval_ranked",
    "answer_delta",
    "turn_completed",
})
REQUIRED_MCP_TOOLS = frozenset({
    "knowledge_search",
    "knowledge_chat",
    "knowledge_get_fragment",
    "knowledge_graph_query",
    "knowledge_get_document_profile",
    "structured_schema_search",
    "structured_get_object",
    "structured_execute_query",
})
_CITATION = re.compile(r"\[(\d{1,3})\]")
_SECRET_TEXT = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]{8,}=*"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"://[^:/\s]+:[^@/\s]+@"),
)


class ServiceChainError(RuntimeError):
    """A public, credential-free service-chain failure."""


@dataclass
class ServiceContext:
    space_id: str
    chunk_id: str
    document_id: str
    version_id: str
    mapping_version_id: str
    plan: dict[str, Any]
    query_ir: dict[str, Any]


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": True, "detail": detail}


def _safe_error(stage: str, exc: Exception) -> str:
    if isinstance(exc, ServiceChainError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{stage} 返回 HTTP {exc.response.status_code}"
    return f"{stage} 失败：{type(exc).__name__}"


def _single_numeric_result(payload: dict[str, Any]) -> float:
    rows = payload.get("rows") or (payload.get("result") or {}).get("rows") or []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ServiceChainError("结构化查询没有返回单行结果")
    numeric: list[float] = []
    for value in rows[0].values():
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric.append(float(str(value).replace(",", "")))
        except ValueError:
            continue
    if len(numeric) != 1:
        raise ServiceChainError("结构化查询结果不是唯一数值")
    return numeric[0]


def _supplier_count_contract(
    mapping: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build a strict semantic count without exposing a physical identifier.

    The full preflight separately runs all natural-language Ground Truth
    questions.  The service-chain check uses an already activated mapping ID
    so REST and MCP validate deterministic Plan/IR execution without spending
    another model call or accepting raw SQL.
    """

    version = mapping.get("active_version") or {}
    manifest = version.get("manifest") or {}
    entity = next(
        (
            row for row in (manifest.get("entities") or [])
            if "供应商" in str(row.get("label") or "")
        ),
        None,
    )
    if not entity:
        raise ServiceChainError("激活映射缺少供应商业务对象")
    attributes = [
        row for row in (manifest.get("attributes") or [])
        if row.get("entity_id") == entity.get("id")
    ]
    name_attribute = next(
        (row for row in attributes if "名称" in str(row.get("label") or "")),
        attributes[0] if attributes else None,
    )
    if not name_attribute:
        raise ServiceChainError("供应商业务对象缺少可计数属性")
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
    return plan, query_ir, str(version.get("id") or "")


def _mcp_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None,
    )
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
    raise ServiceChainError("MCP 工具没有返回结构化对象")


def _parse_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line and data_lines:
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                raise ServiceChainError("DSH 流包含无效 JSON 事件") from exc
            if not isinstance(payload, dict):
                raise ServiceChainError("DSH 流事件不是结构化对象")
            events.append((event_name, payload))
            event_name, data_lines = "message", []
    if data_lines:
        raise ServiceChainError("DSH 流在完整事件结束前中断")
    return events


def _run_turn(
    client: httpx.Client,
    conversation_id: str,
    question: str,
) -> list[tuple[str, dict[str, Any]]]:
    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages",
        headers={"Accept": "text/event-stream"},
        json={"content": question},
    ) as response:
        response.raise_for_status()
        return _parse_sse(response)


def _validate_assistant_citations(
    client: httpx.Client,
    assistant: dict[str, Any],
    *,
    expected_version_id: str | None = None,
) -> int:
    content = str(assistant.get("content") or "").strip()
    citations = [row for row in (assistant.get("citations") or []) if isinstance(row, dict)]
    if assistant.get("status") != "completed" or not content or not citations:
        raise ServiceChainError("Agent 回答未完成或没有可核验引用")
    mentioned = {int(value) for value in _CITATION.findall(content)}
    available = {int(row.get("citation_number") or 0) for row in citations}
    if not mentioned or not mentioned.issubset(available):
        raise ServiceChainError("Agent 回答引用编号与引用投影不一致")
    matched_current = expected_version_id is None
    for citation in citations:
        chunk_id = str(citation.get("chunk_id") or "")
        if not chunk_id:
            raise ServiceChainError("Agent 引用缺少片段标识")
        fragment = client.get(f"/fragments/{chunk_id}")
        fragment.raise_for_status()
        payload = fragment.json()
        if not payload.get("has_access") or not str(payload.get("text") or "").strip():
            raise ServiceChainError("Agent 引用无法打开真实片段")
        if expected_version_id and str(payload.get("version_id") or "") == expected_version_id:
            matched_current = True
    if not matched_current:
        raise ServiceChainError("制度回答没有引用当前有效版本")
    return len(citations)


def _verify_rest(client: httpx.Client) -> tuple[ServiceContext, dict[str, int]]:
    spaces_response = client.get("/spaces")
    spaces_response.raise_for_status()
    space = next(
        (row for row in spaces_response.json() if row.get("code") == SPACE_CODE),
        None,
    )
    if not space:
        raise ServiceChainError("REST 未找到国联演示知识空间")
    space_id = str(space["id"])

    search_response = client.post("/search", json={
        "query": "采购实施细则 供应商准入",
        "space_ids": [space_id],
        "top_k": 5,
        "use_keyword": True,
        "use_vector": True,
        "use_graph": True,
        "use_reranker": False,
        "filters": {},
    })
    search_response.raise_for_status()
    search = search_response.json()
    items = search.get("items") or []
    if not items or not search.get("query_id"):
        raise ServiceChainError("REST 混合检索没有真实召回")
    first = items[0]
    fragment_response = client.get(f"/fragments/{first['chunk_id']}")
    fragment_response.raise_for_status()
    fragment = fragment_response.json()
    if not fragment.get("has_access") or not str(fragment.get("text") or "").strip():
        raise ServiceChainError("REST 片段接口没有返回可访问内容")

    documents_response = client.get("/documents", params={"space_id": space_id})
    documents_response.raise_for_status()
    document = next(
        (row for row in documents_response.json() if row.get("title") == CURRENT_POLICY_TITLE),
        None,
    )
    if not document:
        raise ServiceChainError("REST 未找到当前制度演示文档")
    detail_response = client.get(f"/documents/{document['id']}")
    detail_response.raise_for_status()
    detail = detail_response.json()
    current_version_id = str(detail.get("current_version_id") or "")
    if not current_version_id:
        raise ServiceChainError("当前制度文档没有有效版本")
    profile_response = client.get(f"/versions/{current_version_id}/profile")
    profile_response.raise_for_status()
    profile = profile_response.json()
    if not str(profile.get("summary") or "").strip() or profile.get("quality_score") is None:
        raise ServiceChainError("REST 文档画像缺少摘要或质量分")

    graph_response = client.post("/analysis/visual-query", json={
        "space_ids": [space_id],
        "subject_query": "东方智造",
        "predicate": "供应",
        "object_type": "",
        "include_inferred": True,
        "limit": 20,
    })
    graph_response.raise_for_status()
    graph = graph_response.json()
    if not graph.get("rows") or not graph.get("query_id"):
        raise ServiceChainError("REST 图谱查询没有返回东方智造供应关系")

    mappings_response = client.get("/semantic-mappings", params={"space_id": space_id})
    mappings_response.raise_for_status()
    mappings = mappings_response.json().get("items") or []
    mapping = next(
        (
            row for row in mappings
            if (row.get("active_version") or {}).get("status") == "active"
        ),
        None,
    )
    if not mapping:
        raise ServiceChainError("REST 没有可用的激活数据库语义映射")
    plan, query_ir, mapping_version_id = _supplier_count_contract(mapping)
    structured_response = client.post("/structured-query/execute", json={
        "mapping_version_id": mapping_version_id,
        "plan": plan,
        "query_ir": query_ir,
        "max_rows": 20,
    })
    structured_response.raise_for_status()
    result = structured_response.json()
    if (
        result.get("status") != "succeeded"
        or not result.get("query_run_id")
        or not (result.get("validation") or {}).get("plan_fingerprint")
        or not (result.get("validation") or {}).get("ir_fingerprint")
        or not result.get("source_citations")
        or _single_numeric_result(result) != EXPECTED_SUPPLIER_COUNT
    ):
        raise ServiceChainError("REST 结构化查询未通过 Plan/IR/SQL/结果/引用合约")

    return ServiceContext(
        space_id=space_id,
        chunk_id=str(first["chunk_id"]),
        document_id=str(document["id"]),
        version_id=current_version_id,
        mapping_version_id=mapping_version_id,
        plan=plan,
        query_ir=query_ir,
    ), {
        "search_items": len(items),
        "graph_rows": len(graph.get("rows") or []),
        "structured_rows": len(result.get("rows") or []),
        "structured_citations": len(result.get("source_citations") or []),
    }


def _verify_dsh(
    client: httpx.Client,
    context: ServiceContext,
    *,
    on_conversation: Callable[[str], None],
) -> dict[str, int]:
    created = client.post("/conversations", json={
        "title": "演示预检｜DSH 多轮上下文",
        "space_ids": [context.space_id],
        "use_keyword": True,
        "use_vector": True,
        "use_graph": True,
        "use_reranker": False,
        "top_k": 8,
    })
    created.raise_for_status()
    conversation_id = str(created.json()["id"])
    on_conversation(conversation_id)
    questions = (
        "《集团本部采购实施细则（2025演示现行版）》"
        "主要适用于哪些单位？请引用依据。",
        "它对供应商准入有哪些要求？请继续引用当前有效制度。",
    )
    event_total = 0
    citation_total = 0
    tool_names: set[str] = set()
    for turn, question in enumerate(questions, start=1):
        live = _run_turn(client, conversation_id, question)
        live_types = [name for name, _ in live]
        if not live_types or live_types[-1] != "turn_completed":
            raise ServiceChainError(f"DSH 第 {turn} 轮没有正常完成")
        missing = REQUIRED_DSH_EVENTS - set(live_types)
        if missing:
            raise ServiceChainError(f"DSH 第 {turn} 轮缺少可核验事件")
        detail_response = client.get(f"/conversations/{conversation_id}")
        detail_response.raise_for_status()
        detail = detail_response.json()
        assistant = [
            row for row in (detail.get("messages") or [])
            if row.get("role") == "assistant"
        ][-1]
        if turn == 1 and "适用" not in str(assistant.get("content") or ""):
            raise ServiceChainError("DSH 制度回答没有回应适用范围")
        if turn == 2 and not all(
            value in str(assistant.get("content") or "") for value in ("供应商", "准入")
        ):
            raise ServiceChainError("DSH 指代追问没有回应供应商准入")
        citation_total += _validate_assistant_citations(
            client,
            assistant,
            expected_version_id=context.version_id,
        )
        message_events = [
            row for row in (detail.get("events") or [])
            if row.get("message_id") == assistant.get("id")
        ]
        sequences = [int(row.get("sequence") or 0) for row in message_events]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ServiceChainError("DSH 持久化事件序列重复或乱序")
        if turn == 2 and not any(
            row.get("event_type") == "turn_started"
            and (row.get("payload") or {}).get("has_prior_turns") is True
            for row in message_events
        ):
            raise ServiceChainError("DSH 第二轮没有持久化历史上下文标识")
        turn_tools = {
            str((row.get("payload") or {}).get("name") or "")
            for row in message_events
            if row.get("event_type") in {"tool_started", "tool_finished"}
        } - {""}
        if "knowledge_search" not in turn_tools:
            raise ServiceChainError(f"DSH 第 {turn} 轮没有真实调用知识检索工具")
        tool_names.update(turn_tools)
        event_total += len(message_events)

    refreshed = client.get(f"/conversations/{conversation_id}")
    refreshed.raise_for_status()
    messages = refreshed.json().get("messages") or []
    if (
        len([row for row in messages if row.get("role") == "user"]) != 2
        or len([row for row in messages if row.get("role") == "assistant"]) != 2
    ):
        raise ServiceChainError("DSH 多轮消息在刷新后没有完整恢复")
    return {
        "turns": 2,
        "events": event_total,
        "citations": citation_total,
        "tools": len(tool_names),
    }


async def _verify_mcp_async(
    *,
    token: str,
    mcp_url: str,
    context: ServiceContext,
    conversation_id: str,
) -> dict[str, int | bool]:
    timeout = httpx.Timeout(30, read=900)
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    ) as transport:
        async with streamable_http_client(mcp_url, http_client=transport) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {row.name for row in (await session.list_tools()).tools}
                if not REQUIRED_MCP_TOOLS.issubset(tools):
                    raise ServiceChainError("MCP 缺少主演示必需工具")
                search = _mcp_payload(await session.call_tool("knowledge_search", {
                    "query": "采购实施细则 供应商准入",
                    "space_ids": [context.space_id],
                    "top_k": 3,
                    "use_keyword": True,
                    "use_vector": False,
                    "use_graph": False,
                    "use_reranker": False,
                }))
                fragment = _mcp_payload(await session.call_tool(
                    "knowledge_get_fragment", {"chunk_id": context.chunk_id},
                ))
                graph = _mcp_payload(await session.call_tool("knowledge_graph_query", {
                    "space_ids": [context.space_id],
                    "entity_query": "NexusOne",
                    "limit": 20,
                }))
                profile = _mcp_payload(await session.call_tool(
                    "knowledge_get_document_profile", {"version_id": context.version_id},
                ))
                catalog = _mcp_payload(await session.call_tool("structured_schema_search", {
                    "query": "供应商",
                    "space_ids": [context.space_id],
                    "limit": 10,
                }))
                semantic_objects = catalog.get("semantic_objects") or []
                if not semantic_objects:
                    raise ServiceChainError("MCP 结构化目录没有返回供应商对象")
                semantic_object = semantic_objects[0]
                detail = _mcp_payload(await session.call_tool("structured_get_object", {
                    "semantic_object_id": semantic_object["semantic_object_id"],
                    "mapping_version_id": semantic_object["mapping_version_id"],
                }))
                structured = _mcp_payload(await session.call_tool("structured_execute_query", {
                    "semantic_query_plan": context.plan,
                    "query_ir": context.query_ir,
                    "mapping_version_id": context.mapping_version_id,
                    "max_rows": 20,
                }))
                try:
                    invalid = await session.call_tool("structured_execute_query", {
                        "semantic_query_plan": context.plan,
                        "query_ir": context.query_ir,
                        "mapping_version_id": context.mapping_version_id,
                        "max_rows": 20,
                        "sql": "DROP TABLE suppliers",
                    })
                    raw_sql_rejected = bool(
                        getattr(invalid, "isError", False) or getattr(invalid, "is_error", False)
                    )
                except Exception:
                    raw_sql_rejected = True
                chat = _mcp_payload(await session.call_tool("knowledge_chat", {
                    "message": "采购实施细则对供应商准入有哪些要求？请引用依据。",
                    "conversation_id": conversation_id,
                    "space_ids": [context.space_id],
                }))
    if (
        not search.get("items")
        or fragment.get("has_access") is not True
        or not graph.get("entities")
        or not str(profile.get("summary") or "").strip()
        or not detail.get("attributes")
        or structured.get("status") != "succeeded"
        or _single_numeric_result(structured) != EXPECTED_SUPPLIER_COUNT
        or not structured.get("source_citations")
        or not raw_sql_rejected
        or str(chat.get("conversation_id") or "") != conversation_id
        or chat.get("status") != "completed"
        or not str(chat.get("answer") or "").strip()
        or not chat.get("citations")
        or "turn_completed" not in (chat.get("event_types") or [])
    ):
        raise ServiceChainError("MCP 工具真实调用结果未通过结构化合约")
    return {
        "registered_tools": len(tools),
        "search_items": len(search.get("items") or []),
        "graph_entities": len(graph.get("entities") or []),
        "structured_rows": len(structured.get("rows") or []),
        "chat_events": len(chat.get("event_types") or []),
        "chat_citations": len(chat.get("citations") or []),
        "raw_sql_rejected": raw_sql_rejected,
    }


def _verify_mcp(
    *,
    token: str,
    api_url: str,
    mcp_url: str,
    context: ServiceContext,
    on_conversation: Callable[[str], None],
) -> dict[str, int | bool]:
    with httpx.Client(
        base_url=api_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:
        created = client.post("/conversations", json={
            "title": "演示预检｜MCP 服务链",
            "space_ids": [context.space_id],
        })
        created.raise_for_status()
        conversation_id = str(created.json()["id"])
    on_conversation(conversation_id)
    return asyncio.run(_verify_mcp_async(
        token=token,
        mcp_url=mcp_url,
        context=context,
        conversation_id=conversation_id,
    ))


def _run_cli(
    arguments: list[str],
    *,
    environment: dict[str, str],
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "apps.cli", *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise ServiceChainError(f"CLI {' '.join(arguments[:1])} 返回非零退出码")
    return result


def _verify_cli(
    *,
    token: str,
    api_url: str,
    context: ServiceContext,
    on_conversation: Callable[[str], None],
) -> dict[str, int | bool]:
    with tempfile.TemporaryDirectory(prefix="chuanshen-demo-service-") as temp_dir:
        environment = {
            **os.environ,
            "CHUANSHEN_TOKEN": token,
            "CHUANSHEN_API_URL": api_url,
            "CHUANSHEN_CONFIG": str(Path(temp_dir) / "config.json"),
        }
        with httpx.Client(
            base_url=api_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ) as client:
            created = client.post("/conversations", json={
                "title": "演示预检｜CLI 服务链",
                "space_ids": [context.space_id],
            })
            created.raise_for_status()
            conversation_id = str(created.json()["id"])
        on_conversation(conversation_id)
        spaces_result = _run_cli(["spaces"], environment=environment)
        search_result = _run_cli(
            ["search", "采购实施细则", "--space", context.space_id, "--top-k", "2"],
            environment=environment,
        )
        fragment_result = _run_cli(
            ["fragment", context.chunk_id], environment=environment,
        )
        chat_result = _run_cli(
            [
                "chat", "采购实施细则对供应商准入有哪些要求？请引用依据。",
                "--conversation", conversation_id,
            ],
            environment=environment,
        )
        try:
            spaces = json.loads(spaces_result.stdout)
            search = json.loads(search_result.stdout)
            fragment = json.loads(fragment_result.stdout)
        except json.JSONDecodeError as exc:
            raise ServiceChainError("CLI JSON 输出无法解析") from exc
        conversation_match = re.search(r"conversation_id=([^\s]+)", chat_result.stdout)
        returned_conversation_id = conversation_match.group(1) if conversation_match else ""
        if (
            not any(row.get("code") == SPACE_CODE for row in spaces.get("items") or [])
            or not search.get("items")
            or fragment.get("has_access") is not True
            or returned_conversation_id != conversation_id
        ):
            raise ServiceChainError("CLI 真实命令结果未通过业务合约")
        combined_output = "".join(
            value
            for result in (
                spaces_result, search_result, fragment_result, chat_result,
            )
            for value in (result.stdout, result.stderr)
        )
        if token in combined_output or any(pattern.search(combined_output) for pattern in _SECRET_TEXT):
            raise ServiceChainError("CLI 输出疑似包含访问凭据")
        return {
            "spaces": len(spaces.get("items") or []),
            "search_items": len(search.get("items") or []),
            "fragment_accessible": True,
            "chat_completed": True,
        }


def verify_service_chain(
    *,
    api_url: str,
    mcp_url: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    conversations: list[str] = []
    context: ServiceContext | None = None
    token = ""
    client = httpx.Client(
        base_url=api_url.rstrip("/"),
        timeout=httpx.Timeout(30, read=900),
    )
    started = time.perf_counter()
    try:
        login = client.post("/auth/login", json={"username": username, "password": password})
        if not login.is_success:
            raise ServiceChainError(f"服务链登录返回 HTTP {login.status_code}")
        token = str(login.json().get("access_token") or "")
        if not token:
            raise ServiceChainError("服务链登录没有返回 Token")
        client.headers["Authorization"] = f"Bearer {token}"

        for key, label, operation in (
            ("rest", "REST 知识与结构化服务链", lambda: _verify_rest(client)),
            (
                "dsh",
                "DeepSeek Harness 多轮事件与引用",
                lambda: _verify_dsh(
                    client,
                    context,
                    on_conversation=conversations.append,
                ) if context else None,
            ),
            (
                "mcp",
                "MCP 工具服务链",
                lambda: _verify_mcp(
                    token=token,
                    api_url=api_url,
                    mcp_url=mcp_url,
                    context=context,
                    on_conversation=conversations.append,
                )
                if context else None,
            ),
            (
                "cli",
                "CLI 命令服务链",
                lambda: _verify_cli(
                    token=token,
                    api_url=api_url,
                    context=context,
                    on_conversation=conversations.append,
                )
                if context else None,
            ),
        ):
            stage_started = time.perf_counter()
            if key != "rest" and context is None:
                checks.append(_check(label, False, "REST 前置链路失败，未执行"))
                continue
            try:
                value = operation()
                if value is None:
                    raise ServiceChainError("服务链前置上下文不存在")
                if key == "rest":
                    context, stage_metrics = value
                else:
                    stage_metrics = value
                metrics[key] = {
                    **stage_metrics,
                    "elapsed_ms": round((time.perf_counter() - stage_started) * 1000),
                }
                checks.append(
                    _check(
                        label,
                        True,
                        f"真实调用通过；耗时 {metrics[key]['elapsed_ms']} ms",
                    )
                )
            except Exception as exc:
                checks.append(_check(label, False, _safe_error(label, exc)))
    except Exception as exc:
        checks.append(_check("服务链接入", False, _safe_error("服务链接入", exc)))
    finally:
        for conversation_id in dict.fromkeys(conversations):
            try:
                client.delete(f"/conversations/{conversation_id}")
            except Exception:
                pass
        client.close()

    report = {
        "ready": bool(checks) and all(row["ok"] for row in checks),
        "checks": checks,
        "metrics": metrics,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    serialized = json.dumps(report, ensure_ascii=False)
    if token and token in serialized:
        raise ServiceChainError("服务链报告包含访问凭据，已阻止输出")
    if any(pattern.search(serialized) for pattern in _SECRET_TEXT):
        raise ServiceChainError("服务链报告包含疑似 Secret，已阻止输出")
    return report


def _default_mcp_url(api_url: str) -> str:
    configured = (
        os.getenv("GUOLIAN_DEMO_MCP_URL", "").strip()
        or os.getenv("TEST_MCP", "").strip()
    )
    if configured:
        return configured
    return "http://mcp-server:8091/mcp" if "//api:" in api_url else "http://127.0.0.1:8091/mcp"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="真实验证国联演示 REST、DSH、MCP 与 CLI 服务链",
    )
    value.add_argument("--compact", action="store_true", help="输出单行 JSON")
    value.add_argument("--no-strict", action="store_true", help="失败时仍返回退出码 0")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        username, password = admin_credentials_from_environment()
        api_url = api_url_from_environment()
        result = verify_service_chain(
            api_url=api_url,
            mcp_url=_default_mcp_url(api_url),
            username=username,
            password=password,
        )
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        return 0 if result["ready"] or args.no_strict else 2
    except Exception as exc:
        print(
            json.dumps(
                {"ready": False, "error": _safe_error("服务链验收", exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
