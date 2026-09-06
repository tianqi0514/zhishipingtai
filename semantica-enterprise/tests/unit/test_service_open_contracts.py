from __future__ import annotations

import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from apps import cli
from apps.api.structured_schemas import SemanticQueryIR, SemanticQueryPlan
from apps.mcp import server


ROOT = Path(__file__).resolve().parents[2]


def _mapping_response() -> dict:
    return {
        "items": [{
            "id": "mapping-set-1",
            "name": "演示经营数据映射",
            "space_id": "space-1",
            "source_id": "source-1",
            "active_version": {
                "id": "mapping-version-1",
                "status": "active",
                "schema_version_id": "schema-1",
                "manifest": {
                    "entities": [{
                        "id": "supplier", "label": "供应商", "description": "采购供应商",
                        "fragments": [{"object_id": "physical.suppliers", "identity_column_ids": ["secret-id"]}],
                    }],
                    "attributes": [{
                        "id": "supplier-name", "entity_id": "supplier", "label": "供应商名称",
                        "fragment_id": "supplier-fragment", "column_id": "physical.supplier_name",
                    }],
                    "relationships": [{
                        "id": "supplier-product", "label": "供应产品",
                        "from_entity_id": "supplier", "to_entity_id": "product",
                        "predicates": [{"left": {"column_id": "supplier_id"}}],
                    }],
                },
            },
        }]
    }


def _plan() -> SemanticQueryPlan:
    return SemanticQueryPlan.model_validate({
        "original_question": "一共有多少家供应商？",
        "intent": "统计供应商数量",
        "entity_ids": ["supplier"],
        "outputs": [{
            "position": 1, "label": "供应商数量", "kind": "metric",
            "attribute_ids": ["supplier-name"], "aggregate": "count",
        }],
        "expected_cardinality": "single_value",
    })


def _query_ir() -> SemanticQueryIR:
    return SemanticQueryIR.model_validate({
        "from_entity": {"binding": "s", "entity_id": "supplier"},
        "select": [{
            "expression": {
                "kind": "aggregate", "function": "count", "distinct": True,
                "expression": {"kind": "attribute", "attribute_id": "supplier-name", "binding": "s"},
            },
            "alias": "supplier_count",
        }],
    })


class _FakeChatStream:
    status_code = 200

    def __init__(self, lines: list[str]):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self):
        yield from self.lines


def _run_cli_chat(monkeypatch, terminal_event: str | None, payload: dict | None = None):
    lines = [
        "event: answer_delta",
        'data: {"text":"已生成回答"}',
        "",
    ]
    if terminal_event:
        lines.extend([
            f"event: {terminal_event}",
            f"data: {json.dumps(payload or {}, ensure_ascii=False)}",
            "",
        ])
    monkeypatch.setattr(cli, "_request", lambda *_args, **_kwargs: {"id": "conversation-1"})
    monkeypatch.setattr(cli.httpx, "stream", lambda *_args, **_kwargs: _FakeChatStream(lines))
    monkeypatch.setattr(cli, "_headers", lambda *_args, **_kwargs: {"Authorization": "Bearer redacted"})
    return CliRunner().invoke(cli.app, ["chat", "测试问题"])


def test_mcp_registers_demo_structured_tool_contracts() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    names = {item.name for item in tools}
    assert {
        "structured_schema_search", "structured_get_object", "structured_execute_query", "structured_query",
    } <= names
    execute = next(item for item in tools if item.name == "structured_execute_query")
    properties = execute.inputSchema["properties"]
    assert {"semantic_query_plan", "query_ir", "mapping_version_id", "max_rows"} <= properties.keys()
    assert execute.inputSchema["additionalProperties"] is False
    assert "sql" not in properties
    assert "connection_string" not in properties


def test_mcp_structured_catalog_is_derived_from_authorised_api_projection(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_request(method: str, path: str, **_kwargs):
        calls.append((method, path))
        return _mapping_response()

    monkeypatch.setattr(server, "_request", fake_request)
    result = asyncio.run(server.structured_schema_search("供应商", ["space-1"], [], 10))
    assert calls == [("GET", "/semantic-mappings")]
    assert result["semantic_objects"][0]["semantic_object_id"] == "supplier"
    detail = asyncio.run(server.structured_get_object("supplier", "mapping-version-1"))
    assert detail["mapping_status"] == "active"
    assert detail["attributes"][0]["id"] == "supplier-name"
    encoded = json.dumps(detail, ensure_ascii=False)
    for physical in ("physical.suppliers", "physical.supplier_name", "secret-id", "supplier_id"):
        assert physical not in encoded
    assert "fragments" not in detail["semantic_object"]
    assert "column_id" not in detail["attributes"][0]
    assert "predicates" not in detail["relationships"][0]


def test_mcp_execute_forwards_strict_plan_and_ir_to_fastapi(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(method: str, path: str, *, params=None, payload=None):
        calls.append((method, path, payload))
        if path == "/semantic-mappings":
            return _mapping_response()
        return {"status": "succeeded", "row_count": 1}

    monkeypatch.setattr(server, "_request", fake_request)
    result = asyncio.run(server.structured_execute_query(
        _plan(), _query_ir(), "mapping-version-1", 20
    ))
    assert result["status"] == "succeeded"
    method, path, payload = calls[-1]
    assert (method, path) == ("POST", "/structured-query/execute")
    assert payload and payload["mapping_version_id"] == "mapping-version-1"
    assert payload["plan"]["version"] == "chuanshen.semantic-query-plan/v1"
    assert payload["query_ir"]["version"] == "chuanshen.query-ir/v1"
    assert "sql" not in payload


def test_cli_spaces_lists_only_business_safe_projection(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_request", lambda *_args, **_kwargs: [{
        "id": "space-1", "code": "demo", "name": "演示空间",
        "description": "演示", "effective_permission": "read",
        "internal_field": "must-not-leak",
    }])
    result = CliRunner().invoke(cli.app, ["spaces"])
    assert result.exit_code == 0
    assert "演示空间" in result.stdout
    assert "must-not-leak" not in result.stdout


def test_cli_chat_succeeds_only_after_completed_terminal_event(monkeypatch) -> None:
    result = _run_cli_chat(monkeypatch, "turn_completed", {"duration_ms": 120})
    assert result.exit_code == 0
    assert "已生成回答" in result.stdout
    assert "conversation_id=conversation-1" in result.stdout


def test_cli_chat_returns_nonzero_for_failed_and_cancelled_turns(monkeypatch) -> None:
    failed = _run_cli_chat(monkeypatch, "turn_failed", {"message": "模型暂时不可用"})
    assert failed.exit_code != 0
    assert "生成失败" in failed.output
    assert "conversation_id=" not in failed.output

    cancelled = _run_cli_chat(monkeypatch, "turn_cancelled", {"reason": "user_cancelled"})
    assert cancelled.exit_code != 0
    assert "生成已取消" in cancelled.output
    assert "conversation_id=" not in cancelled.output


def test_cli_chat_returns_nonzero_when_stream_has_no_terminal_event(monkeypatch) -> None:
    result = _run_cli_chat(monkeypatch, None)
    assert result.exit_code != 0
    assert "完成前中断" in result.output


def test_service_open_implementation_never_connects_to_data_stores() -> None:
    mcp_source = (ROOT / "apps/mcp/server.py").read_text(encoding="utf-8")
    cli_source = (ROOT / "apps/cli.py").read_text(encoding="utf-8")
    for forbidden in ("sqlalchemy", "psycopg", "pymysql", "qdrant", "falkordb", "opensearch"):
        assert forbidden not in mcp_source.casefold()
        assert forbidden not in cli_source.casefold()
    assert 'os.getenv("MCP_ALLOWED_HOSTS"' in mcp_source
    compose_source = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "MCP_ALLOWED_HOSTS: ${MCP_ALLOWED_HOSTS:-}" in compose_source
