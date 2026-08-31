from __future__ import annotations

import asyncio
import json
import os
from contextvars import ContextVar
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


API_BASE = os.getenv("PLATFORM_API", "http://api:8080/api/v1").rstrip("/")
PORT = int(os.getenv("MCP_PORT", "8091"))
REQUEST_TIMEOUT = float(os.getenv("MCP_REQUEST_TIMEOUT", "600"))
authorization_header: ContextVar[str | None] = ContextVar("authorization_header", default=None)

mcp = FastMCP(
    "传神智库",
    instructions="通过传神智库 FastAPI 的授权入口检索、对话和读取知识依据。",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["mcp-server:8091", "localhost:8091", "127.0.0.1:8091"]
    ),
)


class BearerContextMiddleware:
    """Bind the caller's bearer token to one MCP HTTP request without storing it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        token = authorization_header.set(headers.get("authorization"))
        try:
            await self.app(scope, receive, send)
        finally:
            authorization_header.reset(token)


def _headers() -> dict[str, str]:
    authorization = authorization_header.get()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ValueError("MCP 请求必须携带传神智库 Bearer Token")
    return {"Authorization": authorization}


async def _request(method: str, path: str, *, params=None, payload=None) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_headers()) as client:
        response = await client.request(method, f"{API_BASE}{path}", params=params, json=payload)
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:500]}
    if response.status_code >= 400:
        raise ValueError(str(body.get("detail") or f"知识平台请求失败 ({response.status_code})"))
    return body


@mcp.tool()
async def knowledge_search(
    query: str,
    space_ids: list[str] | None = None,
    top_k: int = 10,
    use_keyword: bool = True,
    use_vector: bool = True,
    use_graph: bool = True,
    use_reranker: bool = True,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行按最终分数排序的全文、向量和图谱融合检索。"""
    return await _request(
        "POST",
        "/search",
        payload={
            "query": query,
            "space_ids": space_ids or [],
            "top_k": top_k,
            "use_keyword": use_keyword,
            "use_vector": use_vector,
            "use_graph": use_graph,
            "use_reranker": use_reranker,
            "filters": filters or {},
        },
    )


@mcp.tool()
async def knowledge_chat(
    message: str,
    conversation_id: str | None = None,
    space_ids: list[str] | None = None,
) -> dict[str, Any]:
    """通过 DeepSeek Harness Agent 开始或继续一轮知识对话。"""
    if not conversation_id:
        conversation = await _request(
            "POST",
            "/conversations",
            payload={"title": "MCP 会话", "space_ids": space_ids or []},
        )
        conversation_id = str(conversation["id"])
    events: list[dict[str, Any]] = []
    final_status = "failed"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_headers()) as client:
        async with client.stream(
            "POST",
            f"{API_BASE}/conversations/{conversation_id}/messages",
            json={"content": message},
        ) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", errors="replace")[:500]
                raise ValueError(f"对话请求失败 ({response.status_code})：{detail}")
            event_type = "message"
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    payload = json.loads("\n".join(data_lines))
                    events.append({"type": event_type, "data": payload})
                    if event_type.startswith("turn_"):
                        final_status = event_type.removeprefix("turn_")
                    event_type, data_lines = "message", []
    detail = await _request("GET", f"/conversations/{conversation_id}")
    assistant = next((item for item in reversed(detail.get("messages") or []) if item.get("role") == "assistant"), None)
    return {
        "conversation_id": conversation_id,
        "status": final_status,
        "answer": (assistant or {}).get("content", ""),
        "citations": (assistant or {}).get("citations", []),
        "retrieval_traces": (assistant or {}).get("traces", []),
        "event_types": [item["type"] for item in events],
    }


@mcp.tool()
async def knowledge_get_fragment(chunk_id: str) -> dict[str, Any]:
    """读取一个真实知识片段及文档、页码、结构位置和版本来源。"""
    return await _request("GET", f"/fragments/{chunk_id}")


@mcp.tool()
async def knowledge_graph_query(
    space_ids: list[str],
    entity_query: str = "",
    relation_query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """查询获授权知识空间中的实体与关系事实。"""
    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for space_id in space_ids:
        entity_page = await _request("GET", "/knowledge/entities", params={"space_id": space_id, "limit": 500})
        fact_page = await _request("GET", "/knowledge/facts", params={"space_id": space_id, "limit": 500})
        entities.extend(entity_page.get("items") or [])
        facts.extend(fact_page.get("items") or [])
    entity_term = entity_query.casefold().strip()
    relation_term = relation_query.casefold().strip()
    if entity_term:
        entities = [
            item for item in entities
            if entity_term in str(item.get("canonical_name") or "").casefold()
            or any(entity_term in str(alias).casefold() for alias in item.get("aliases") or [])
        ]
        ids = {item.get("id") for item in entities}
        facts = [item for item in facts if item.get("subject_entity_id") in ids or item.get("object_entity_id") in ids]
    if relation_term:
        facts = [item for item in facts if relation_term in str(item.get("predicate") or "").casefold()]
    return {"entities": entities[:limit], "facts": facts[:limit]}


@mcp.tool()
async def knowledge_reason(
    rule_set_id: str,
    space_ids: list[str] | None = None,
    publish: bool = False,
    max_results: int = 100,
) -> dict[str, Any]:
    """使用 Semantica 规则引擎运行一个规则集，并返回推导事实与证据链。"""
    run = await _request(
        "POST",
        "/analysis/inference-runs",
        payload={
            "rule_set_id": rule_set_id,
            "space_ids": space_ids or [],
            "mode": "publish" if publish else "preview",
            "max_results": max_results,
        },
    )
    for _ in range(180):
        job = await _request("GET", f"/jobs/{run['job_id']}")
        if job.get("status") == "succeeded":
            return await _request("GET", f"/analysis/inference-runs/{run['id']}")
        if job.get("status") == "failed":
            raise ValueError(str(job.get("error_message") or "知识推理失败"))
        await asyncio.sleep(1)
    raise ValueError("知识推理等待超时，任务仍在后台运行")


@mcp.tool()
async def knowledge_sparql(space_ids: list[str], query: str) -> dict[str, Any]:
    """对获授权知识空间的事实及已发布推导事实执行只读 SPARQL。"""
    return await _request("POST", "/analysis/sparql", payload={"space_ids": space_ids, "query": query})


@mcp.tool()
async def knowledge_get_document_profile(
    document_id: str | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    """读取文档版本的治理画像；document_id 与 version_id 必须提供一个。"""
    if bool(document_id) == bool(version_id):
        raise ValueError("document_id 与 version_id 必须且只能提供一个")
    if document_id:
        document = await _request("GET", f"/documents/{document_id}")
        versions = document.get("versions") or []
        if not versions:
            raise ValueError("文档没有可用版本")
        version_id = str(versions[0]["id"])
    return await _request("GET", f"/versions/{version_id}/profile")


def main() -> None:
    app = BearerContextMiddleware(mcp.streamable_http_app())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
