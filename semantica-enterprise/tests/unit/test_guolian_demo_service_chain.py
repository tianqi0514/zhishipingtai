from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest

from scripts.demo.verify_guolian_demo import parser as full_parser
from scripts.demo.verify_guolian_conversation import (
    TURN_CASES,
    _is_explicit_insufficient_evidence_answer,
    _validate_structured_projection,
    verify_eight_turn_conversation,
)
from scripts.demo.verify_guolian_service_chain import (
    EXPECTED_SUPPLIER_COUNT,
    REQUIRED_DSH_EVENTS,
    REQUIRED_MCP_TOOLS,
    ServiceChainError,
    _default_mcp_url,
    _parse_sse,
    _single_numeric_result,
    _supplier_count_contract,
    _validate_assistant_citations,
    parser,
    verify_service_chain,
)


def test_service_chain_requires_all_public_surfaces_and_real_dsh_events() -> None:
    assert REQUIRED_DSH_EVENTS == {
        "turn_started", "step_started", "tool_started", "retrieval_started",
        "tool_finished", "retrieval_ranked", "answer_delta", "turn_completed",
    }
    assert REQUIRED_MCP_TOOLS == {
        "knowledge_search", "knowledge_chat", "knowledge_get_fragment",
        "knowledge_graph_query", "knowledge_get_document_profile",
        "structured_schema_search", "structured_get_object", "structured_execute_query",
    }
    source = inspect.getsource(verify_service_chain)
    for stage in ("rest", "dsh", "mcp", "cli"):
        assert f'"{stage}"' in source
    assert 'all(row["ok"] for row in checks)' in source


def test_sse_parser_requires_complete_structured_events() -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://example.invalid/turn"),
        content=(
            'event: turn_started\ndata: {"turn": 1}\n\n'
            'event: answer_delta\ndata: {"text": "依据"}\n\n'
            'event: turn_completed\ndata: {"duration_ms": 12}\n\n'
        ).encode(),
    )
    assert [name for name, _ in _parse_sse(response)] == [
        "turn_started", "answer_delta", "turn_completed",
    ]

    truncated = httpx.Response(
        200,
        request=httpx.Request("POST", "http://example.invalid/turn"),
        content=b'event: answer_delta\ndata: {"text":"unfinished"}',
    )
    with pytest.raises(ServiceChainError, match="中断"):
        _parse_sse(truncated)


def test_numeric_result_rejects_non_unique_or_non_numeric_answers() -> None:
    assert _single_numeric_result({"rows": [{"count": "5"}]}) == EXPECTED_SUPPLIER_COUNT
    assert _single_numeric_result({"result": {"rows": [{"count": 5}]}}) == EXPECTED_SUPPLIER_COUNT
    with pytest.raises(ServiceChainError):
        _single_numeric_result({"rows": [{"name": "不是数值"}]})
    with pytest.raises(ServiceChainError):
        _single_numeric_result({"rows": [{"a": 1, "b": 2}]})


def test_deterministic_service_contract_uses_only_semantic_ids() -> None:
    plan, query_ir, version_id = _supplier_count_contract({
        "active_version": {
            "id": "mapping-v1",
            "manifest": {
                "entities": [{"id": "supplier-entity", "label": "供应商"}],
                "attributes": [{
                    "id": "supplier-name", "entity_id": "supplier-entity", "label": "供应商名称",
                }],
            },
        },
    })
    assert version_id == "mapping-v1"
    assert plan["entity_ids"] == ["supplier-entity"]
    assert query_ir["select"][0]["expression"]["distinct"] is True
    serialized = json.dumps({"plan": plan, "query_ir": query_ir}, ensure_ascii=False)
    assert "suppliers" not in serialized
    assert '"sql"' not in serialized.casefold()
    assert "DROP TABLE" not in serialized.upper()


def test_citation_validation_opens_real_fragment_and_matches_current_version() -> None:
    class FakeClient:
        def get(self, path):
            assert path == "/fragments/chunk-1"
            return httpx.Response(
                200,
                request=httpx.Request("GET", f"http://example.invalid{path}"),
                json={
                    "has_access": True,
                    "text": "现行制度适用于集团本部。",
                    "version_id": "version-current",
                },
            )

    count = _validate_assistant_citations(
        FakeClient(),
        {
            "status": "completed",
            "content": "适用范围见依据[1]。",
            "citations": [{"citation_number": 1, "chunk_id": "chunk-1"}],
        },
        expected_version_id="version-current",
    )
    assert count == 1
    with pytest.raises(ServiceChainError, match="当前有效版本"):
        _validate_assistant_citations(
            FakeClient(),
            {
                "status": "completed",
                "content": "适用范围见依据[1]。",
                "citations": [{"citation_number": 1, "chunk_id": "chunk-1"}],
            },
            expected_version_id="different-version",
        )


def test_preflight_defaults_to_real_service_chain_and_allows_explicit_non_release_skip() -> None:
    assert parser().parse_args([]).no_strict is False
    defaults = full_parser().parse_args([])
    assert defaults.skip_service_chain_tests is False
    skipped = full_parser().parse_args(["--skip-service-chain-tests"])
    assert skipped.skip_service_chain_tests is True

    shell = Path("scripts/demo/preflight_guolian_demo.sh").read_text(encoding="utf-8")
    assert "import httpx, sqlalchemy, mcp" in shell
    assert "--skip-service-chain-tests" in shell


def test_service_verifier_has_no_direct_business_store_access() -> None:
    source = Path("scripts/demo/verify_guolian_service_chain.py").read_text(encoding="utf-8").casefold()
    for forbidden in (
        "sessionlocal", "sqlalchemy", "opensearch", "qdrant", "falkordb",
        "psycopg", "pymysql", "database_url",
    ):
        assert forbidden not in source
    assert "apps.cli" in source
    assert "streamable_http_client" in source
    assert 'client.delete(f"/conversations/{conversation_id}")' in source
    json.dumps({"ok": True}, ensure_ascii=False)


def test_eight_turn_demo_contract_covers_every_business_evidence_channel() -> None:
    assert len(TURN_CASES) == 8
    assert len({row["id"] for row in TURN_CASES}) == 8
    by_id = {row["id"]: row for row in TURN_CASES}
    assert by_id["policy-scope"]["required_tools"] == {"knowledge_search"}
    assert by_id["structured-by-org"]["required_tools"] == {
        "structured_schema_search", "structured_execute_query",
    }
    assert by_id["document-plus-data"]["required_tools"] == {
        "knowledge_search", "structured_schema_search", "structured_execute_query",
    }
    assert by_id["graph-risk-path"]["required_tools"] == {
        "knowledge_search", "knowledge_graph_query", "knowledge_reason",
    }
    assert by_id["insufficient-evidence"]["insufficient_evidence"] is True


def test_insufficient_evidence_acceptance_recognizes_clear_refusal_without_canned_copy() -> None:
    assert _is_explicit_insufficient_evidence_answer(
        "当前空间没有利润指标，因此无法提供集团明年的实际利润目标。"
    )
    assert not _is_explicit_insufficient_evidence_answer(
        "国联集团明年的实际利润目标是 2,100,000 元。"
    )


def test_structured_projection_requires_mentioned_persisted_query_run() -> None:
    assistant = {
        "content": "按实时数据计算，结果为 600000 元【数据 1】。",
        "structured_citations": [{"citation_number": 1, "query_run_id": "run-1"}],
    }
    events = [
        {"event_type": name}
        for name in (
            "structured_schema_search_started", "structured_schema_search_finished",
            "structured_plan_started", "structured_plan_validated", "structured_ir_validated",
            "structured_query_compiled", "structured_query_started", "structured_query_finished",
        )
    ]
    assert _validate_structured_projection(assistant, events) == 1

    missing_run = {
        **assistant,
        "structured_citations": [{"citation_number": 1, "query_run_id": None}],
    }
    with pytest.raises(ServiceChainError, match="QueryRun"):
        _validate_structured_projection(missing_run, events)

    with pytest.raises(ServiceChainError, match="Plan/IR"):
        _validate_structured_projection(assistant, events[:-1])


def test_eight_turn_verifier_uses_only_public_interfaces_and_redacted_report() -> None:
    source = inspect.getsource(verify_eight_turn_conversation).casefold()
    for forbidden in (
        "sessionlocal", "sqlalchemy", "opensearch", "qdrant", "falkordb",
        "psycopg", "pymysql", "database_url",
    ):
        assert forbidden not in source
    assert 'client.post("/conversations"' in source
    assert 'client.get(f"/conversations/{conversation_id}")' in source
    assert 'client.delete(f"/conversations/{conversation_id}")' in source
    assert '"answer"' not in source


def test_harness_runtime_mounts_upstream_long_session_compaction() -> None:
    patch = Path("integrations/deepseek-harness/cordis.patch.yml").read_text(encoding="utf-8")
    runtime = Path("integrations/deepseek-harness/runtime.js").read_text(encoding="utf-8")
    for package in (
        "@deepseek-ai/dsh-token-meter",
        "@deepseek-ai/dsh-compaction-tool-result-pruner",
        "@deepseek-ai/dsh-compaction-basic",
    ):
        assert package in patch
    assert "maxOverflowRetries: 2" in patch
    assert "DSH_MODEL_CONTEXT_WINDOW" in runtime
    assert "modelContextWindow(model)" in runtime
