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
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase

from apps.api.structured_schemas import SemanticQueryIR, SemanticQueryPlan


API_BASE = os.getenv("PLATFORM_API", "http://api:8080/api/v1").rstrip("/")
PORT = int(os.getenv("MCP_PORT", "8091"))
REQUEST_TIMEOUT = float(os.getenv("MCP_REQUEST_TIMEOUT", "600"))
MCP_ALLOWED_HOSTS = [
    "mcp-server:8091", "localhost:8091", "127.0.0.1:8091",
    *[item.strip() for item in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if item.strip()],
]
authorization_header: ContextVar[str | None] = ContextVar("authorization_header", default=None)

# The MCP SDK argument wrapper otherwise ignores unknown top-level keys even
# when nested Pydantic contracts use ``extra=forbid``.  Make every registered
# public tool fail closed so a caller cannot smuggle SQL or credential-shaped
# fields alongside an otherwise valid request.
ArgModelBase.model_config["extra"] = "forbid"
ArgModelBase.model_rebuild(force=True)

mcp = FastMCP(
    "传神智库",
    instructions="通过传神智库 FastAPI 的授权入口检索、对话、推理并安全查询结构化经营数据。",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=MCP_ALLOWED_HOSTS
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


async def _active_mapping_version(mapping_version_id: str) -> dict[str, Any]:
    """Return one authorised active mapping version through the public API.

    MCP deliberately does not read the platform database.  The list endpoint
    already applies tenant and knowledge-space permissions, and this helper
    only selects one active version from that authorised projection.
    """
    mappings = await _request("GET", "/semantic-mappings")
    for mapping in mappings.get("items") or []:
        active = mapping.get("active_version") or {}
        if str(active.get("id") or "") == mapping_version_id:
            return active
    raise ValueError("语义映射版本不存在、未激活或当前用户无权访问")


def _business_entity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("id", "ontology_term_id", "label", "description")
    }


def _business_attribute(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id", "ontology_term_id", "entity_id", "label", "semantic_type",
            "is_measure", "aliases", "business_definition", "default_aggregate",
            "required_filters", "required_relationships", "confidence",
        )
    }


def _business_relationship(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id", "ontology_term_id", "label", "description", "from_entity_id", "to_entity_id",
            "cardinality", "required_filters", "confidence",
        )
    }


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


@mcp.tool()
async def structured_schema_search(
    query: str,
    space_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """在当前用户可读范围内查找已激活的结构化语义对象。"""
    if not 1 <= limit <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    mappings = await _request("GET", "/semantic-mappings")
    allowed_spaces = set(space_ids or [])
    allowed_sources = set(source_ids or [])
    query_text = query.casefold().strip()
    query_tokens = [item for item in query_text.split() if item]
    items: list[dict[str, Any]] = []
    for mapping in mappings.get("items") or []:
        active = mapping.get("active_version") or {}
        if active.get("status") != "active":
            continue
        if allowed_spaces and str(mapping.get("space_id")) not in allowed_spaces:
            continue
        if allowed_sources and str(mapping.get("source_id")) not in allowed_sources:
            continue
        manifest = active.get("manifest") or {}
        attributes_by_entity: dict[str, list[dict[str, Any]]] = {}
        for attribute in manifest.get("attributes") or []:
            attributes_by_entity.setdefault(str(attribute.get("entity_id") or ""), []).append(attribute)
        relationships = manifest.get("relationships") or []
        for entity in manifest.get("entities") or []:
            entity_id = str(entity.get("id") or "")
            attributes = attributes_by_entity.get(entity_id, [])
            related = [
                item for item in relationships
                if entity_id in {str(item.get("from_entity_id") or ""), str(item.get("to_entity_id") or "")}
            ]
            terms = [
                entity_id,
                str(entity.get("label") or ""),
                str(entity.get("description") or ""),
                *(str(item.get("label") or "") for item in attributes),
                *(str(item.get("label") or "") for item in related),
                str(mapping.get("name") or ""),
            ]
            searchable = " ".join(terms).casefold()
            token_score = sum(item in searchable for item in query_tokens) / max(1, len(query_tokens))
            phrase_score = 1.0 if query_text and query_text in searchable else 0.0
            label_score = min(1.0, sum(
                bool(term and len(term.strip()) >= 2 and term.casefold() in query_text)
                for term in terms
            ) / 2)
            score = max(token_score, phrase_score, label_score)
            if score <= 0:
                continue
            items.append({
                "semantic_object_id": entity_id,
                "label": entity.get("label"),
                "description": entity.get("description"),
                "attribute_ids": [item.get("id") for item in attributes],
                "relationship_ids": [item.get("id") for item in related],
                "mapping_version_id": active.get("id"),
                "source_id": mapping.get("source_id"),
                "space_id": mapping.get("space_id"),
                "score": round(float(score), 4),
            })
    items.sort(key=lambda item: (item["score"], str(item.get("label") or "")), reverse=True)
    selected = items[:limit]
    return {
        "query": query,
        "semantic_objects": selected,
        "mapping_versions": sorted({str(item["mapping_version_id"]) for item in selected}),
        "warnings": [] if selected else ["未找到与问题匹配的已激活结构化语义对象"],
    }


@mcp.tool()
async def structured_get_object(
    semantic_object_id: str,
    mapping_version_id: str,
) -> dict[str, Any]:
    """读取一个已激活映射中的业务对象、属性和关系定义。"""
    version = await _active_mapping_version(mapping_version_id)
    manifest = version.get("manifest") or {}
    entity = next(
        (item for item in manifest.get("entities") or [] if str(item.get("id") or "") == semantic_object_id),
        None,
    )
    if entity is None:
        raise ValueError("业务对象不存在或当前用户无权访问")
    attributes = [
        item for item in manifest.get("attributes") or []
        if str(item.get("entity_id") or "") == semantic_object_id
    ]
    relationships = [
        item for item in manifest.get("relationships") or []
        if semantic_object_id in {
            str(item.get("from_entity_id") or ""), str(item.get("to_entity_id") or "")
        }
    ]
    return {
        # The public MCP projection intentionally strips entity fragments,
        # object/column bindings, join predicates and mapping evidence.  Only
        # FastAPI's deterministic compiler is allowed to resolve those
        # physical details while executing a validated semantic query.
        "semantic_object": _business_entity(entity),
        "attributes": [_business_attribute(item) for item in attributes],
        "relationships": [_business_relationship(item) for item in relationships],
        "relationship_paths": [{
            "relationship_id": item.get("id"),
            "direction": "outgoing" if str(item.get("from_entity_id") or "") == semantic_object_id else "incoming",
            "other_entity_id": item.get("to_entity_id")
            if str(item.get("from_entity_id") or "") == semantic_object_id else item.get("from_entity_id"),
        } for item in relationships],
        "mapping_version_id": version.get("id"),
        "mapping_status": version.get("status"),
        "schema_version_id": version.get("schema_version_id"),
    }


@mcp.tool()
async def structured_execute_query(
    semantic_query_plan: SemanticQueryPlan,
    query_ir: SemanticQueryIR,
    mapping_version_id: str,
    max_rows: int = 100,
) -> dict[str, Any]:
    """校验并执行严格 Plan/IR；不接受原始 SQL 或物理数据库凭据。"""
    if not 1 <= max_rows <= 1000:
        raise ValueError("max_rows 必须在 1 到 1000 之间")
    # Resolve the mapping first so an inactive, stale or unauthorised version
    # is rejected before the execution request is made.
    await _active_mapping_version(mapping_version_id)
    return await _request(
        "POST",
        "/structured-query/execute",
        payload={
            "mapping_version_id": mapping_version_id,
            "plan": semantic_query_plan.model_dump(mode="json"),
            "query_ir": query_ir.model_dump(mode="json"),
            "max_rows": max_rows,
        },
    )


@mcp.tool()
async def structured_query(
    mapping_version_id: str,
    question: str,
    max_rows: int = 100,
) -> dict[str, Any]:
    """用已激活本体映射执行安全、只读的自然语言结构化查询。

    MCP 客户端不能传入 SQL、物理表名或字段名。FastAPI 会生成并严格
    校验 Semantic Query Plan 与 Query IR，再确定性编译参数化 SQL。
    """
    if not 1 <= max_rows <= 1000:
        raise ValueError("max_rows 必须在 1 到 1000 之间")
    return await _request(
        "POST",
        "/structured-query/natural-language",
        payload={
            "mapping_version_id": mapping_version_id,
            "question": question,
            "execute": True,
            "max_rows": max_rows,
        },
    )


def main() -> None:
    app = BearerContextMiddleware(mcp.streamable_http_app())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
