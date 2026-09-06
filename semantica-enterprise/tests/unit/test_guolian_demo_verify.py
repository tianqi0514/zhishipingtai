from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.demo.verify_guolian_demo import (
    DEMO_LLM_SCENES,
    REQUIRED_MODEL_SCENES,
    SEARCH_MODE_CASES,
    _empty_preview_contract,
    _fixture_checksum_audit,
    _graph_evidence_ground_truth_contract,
    _latest_failed_jobs,
    _markdown_was_parsed_as_text,
    _masked_preview_contract,
    _model_route_audit,
    _no_primary_key_preview_contract,
    _release_counts,
    _report_has_secret,
    _search_result_contract,
    _source_sync_audit,
    _structured_rows_match,
    _timed_rows,
    _verify_structured_api_ground_truth,
    parser,
)
from scripts.demo.guolian_demo import DEMO_DOCUMENT_NAMES, DEMO_ROOT


def test_fixture_checksum_audit_rejects_stale_current_document_version() -> None:
    versions = {
        title: {"sha256": hashlib.sha256((DEMO_ROOT / title).read_bytes()).hexdigest()}
        for title in DEMO_DOCUMENT_NAMES
    }
    matched, stale = _fixture_checksum_audit(versions)
    assert len(matched) == len(DEMO_DOCUMENT_NAMES)
    assert stale == []

    versions["扫描采购审批单（演示版）.jpg"] = {"sha256": "0" * 64}
    matched, stale = _fixture_checksum_audit(versions)
    assert len(matched) == len(DEMO_DOCUMENT_NAMES) - 1
    assert stale == ["扫描采购审批单（演示版）.jpg"]


def test_graph_evidence_contract_requires_exact_fact_document_path_and_terms() -> None:
    specs = [{
        "fact_id": "FACT-DEMO-008",
        "subject": "数字科技公司",
        "predicate": "负责",
        "object": "智慧流程中枢项目",
        "source_title": "智慧流程中枢建设说明（演示版）.rst",
        "chunk_structural_path": "document",
        "evidence_terms": ["数字科技公司", "负责", "智慧流程中枢项目"],
        "required_for_inference": True,
    }]
    facts = [{
        "id": "fact-current",
        "subject_name": "数字科技公司",
        "predicate": "负责",
        "object_name": "智慧流程中枢项目",
        "source_chunk_id": "chunk-current",
        "origin_type": "asserted",
        "status": "published",
    }]
    chunks = {
        "chunk-current": {
            "document_title": "智慧流程中枢建设说明（演示版）.rst",
            "structural_path": "document",
            "text": "智慧流程中枢项目由数字科技公司负责建设。",
        }
    }

    passed = _graph_evidence_ground_truth_contract(specs, facts, chunks)
    assert passed["passed"] == 1
    assert passed["required_passed"] == 1
    assert passed["failures"] == []

    # Semantic extraction can independently discover the same business triple
    # from another document.  Ground Truth remains valid when exactly one fact
    # still points to the declared source evidence.
    duplicate_from_other_source = [
        *facts,
        {
            **facts[0],
            "id": "fact-extracted-elsewhere",
            "source_chunk_id": "chunk-elsewhere",
        },
    ]
    with_other_source = {
        **chunks,
        "chunk-elsewhere": {
            **chunks["chunk-current"],
            "document_title": "另一份真实材料.md",
        },
    }
    passed = _graph_evidence_ground_truth_contract(
        specs, duplicate_from_other_source, with_other_source,
    )
    assert passed["passed"] == 1
    assert passed["failures"] == []

    # A human provenance overlay may make two independently-created facts
    # converge on the same verified chunk.  This is one evidence source, not
    # two ambiguous sources.
    duplicate_same_evidence = [
        *facts,
        {**facts[0], "id": "fact-same-evidence"},
    ]
    passed = _graph_evidence_ground_truth_contract(
        specs, duplicate_same_evidence, chunks,
    )
    assert passed["passed"] == 1
    assert passed["failures"] == []

    ambiguous = {
        **chunks,
        "chunk-copy": dict(chunks["chunk-current"]),
    }
    duplicate_exact_source = [
        *facts,
        {**facts[0], "id": "fact-exact-copy", "source_chunk_id": "chunk-copy"},
    ]
    failed = _graph_evidence_ground_truth_contract(
        specs, duplicate_exact_source, ambiguous,
    )
    assert failed["failures"] == [{
        "fact_id": "FACT-DEMO-008", "reason": "evidence_ambiguous",
    }]

    wrong_document = {
        "chunk-current": {**chunks["chunk-current"], "document_title": "无关材料.txt"}
    }
    failed = _graph_evidence_ground_truth_contract(specs, facts, wrong_document)
    assert failed["passed"] == 0
    assert failed["failures"] == [{
        "fact_id": "FACT-DEMO-008", "reason": "source_document_mismatch",
    }]

    missing_term = {
        "chunk-current": {**chunks["chunk-current"], "text": "这里只提到了项目。"}
    }
    failed = _graph_evidence_ground_truth_contract(specs, facts, missing_term)
    assert failed["passed"] == 0
    assert failed["failures"] == [{
        "fact_id": "FACT-DEMO-008", "reason": "evidence_terms_missing",
    }]


def test_graph_evidence_contract_rejects_legacy_reverse_responsibility_relation() -> None:
    specs = [{
        "fact_id": "FACT-DEMO-008",
        "subject": "数字科技公司",
        "predicate": "负责",
        "object": "智慧流程中枢项目",
        "source_title": "职责.rst",
        "chunk_structural_path": "document",
        "evidence_terms": ["数字科技公司", "负责", "智慧流程中枢项目"],
        "required_for_inference": False,
    }]
    facts = [
        {
            "subject_name": "数字科技公司", "predicate": "负责",
            "object_name": "智慧流程中枢项目", "source_chunk_id": "chunk",
            "origin_type": "asserted", "status": "published",
        },
        {
            "subject_name": "智慧流程中枢项目", "predicate": "负责",
            "object_name": "数字科技公司", "source_chunk_id": "legacy",
            "origin_type": "asserted", "status": "published",
        },
    ]
    result = _graph_evidence_ground_truth_contract(
        specs,
        facts,
        {"chunk": {
            "document_title": "职责.rst", "structural_path": "document",
            "text": "数字科技公司负责智慧流程中枢项目。",
        }},
    )
    assert result["legacy_reverse_active"] is True
    assert {row["reason"] for row in result["failures"]} == {
        "legacy_reverse_relation_active",
    }


def test_release_check_requires_real_published_rows_in_every_projection() -> None:
    assert _release_counts({"graphs": [], "indexes": [], "knowledge": []}) == {
        "graphs": 0,
        "indexes": 0,
        "knowledge": 0,
    }
    assert _release_counts({
        "graphs": [{"status": "published"}, {"status": "failed"}],
        "indexes": [{"status": "published"}],
        "knowledge": [{"status": "published"}],
    }) == {"graphs": 1, "indexes": 1, "knowledge": 1}


def test_markdown_contract_proves_text_parser_and_rejects_ocr_elements() -> None:
    version = {
        "filename": "制度.md",
        "content_type": "text/markdown",
        "parse_summary": {"parser": "text"},
    }
    ok, detail = _markdown_was_parsed_as_text(
        version,
        [{"element_type": "heading"}, {"element_type": "paragraph"}],
    )
    assert ok is True
    assert "parser=text" in detail

    wrong_parser, _ = _markdown_was_parsed_as_text(
        {**version, "parse_summary": {"parser": "docling"}},
        [{"element_type": "paragraph"}],
    )
    ocr_element, _ = _markdown_was_parsed_as_text(
        version,
        [{"element_type": "keyframe_ocr"}],
    )
    assert wrong_parser is False
    assert ocr_element is False


def test_media_timing_requires_numeric_ordered_ranges() -> None:
    rows = [
        {"time_start": 0.0, "time_end": 4.2},
        {"time_start": 7, "time_end": 6},
        {"time_start": None, "time_end": 10},
        {"time_start": "0", "time_end": "3"},
    ]
    assert _timed_rows(rows, start="time_start", end="time_end") == [rows[0]]


def test_only_latest_unresolved_failure_blocks_preflight() -> None:
    jobs = [
        {
            "id": "new-success", "job_type": "process_knowledge", "status": "succeeded",
            "input": {"version_id": "v1"},
        },
        {
            "id": "old-failure", "job_type": "process_knowledge", "status": "failed",
            "input": {"version_id": "v1"},
        },
        {
            "id": "still-failed", "job_type": "parse_document", "status": "failed",
            "input": {"version_id": "v2"},
        },
    ]
    unresolved, historical = _latest_failed_jobs(jobs)
    assert historical == 2
    assert [row["id"] for row in unresolved] == ["still-failed"]

    unresolved, historical = _latest_failed_jobs(
        jobs,
        current_version_ids={"v1", "v3"},
    )
    assert historical == 2
    assert unresolved == []

    unresolved, _ = _latest_failed_jobs(
        jobs,
        current_version_ids={"v1", "v2"},
    )
    assert [row["id"] for row in unresolved] == ["still-failed"]


def _routed_model_fixture():
    models = [
        {
            "id": "qwen", "name": "阿里云千问", "model_name": "qwen3.5-plus",
            "model_kind": "llm", "enabled": True, "last_test_status": "success",
        },
        {
            "id": "bge", "name": "本地 BGE", "model_name": "bge-small-zh-v1.5",
            "model_kind": "embedding", "enabled": True, "last_test_status": "success",
        },
        {
            "id": "sensevoice", "name": "本地 SenseVoice", "model_name": "SenseVoiceSmall",
            "model_kind": "asr", "enabled": True, "last_test_status": "success",
        },
        {
            "id": "vision", "name": "千问视觉", "model_name": "qwen3.5-plus",
            "model_kind": "vision", "enabled": True, "last_test_status": "success",
        },
        {
            "id": "internal", "name": "内网 Qwen", "model_name": "Qwen3.8-27B-NVFP4",
            "model_kind": "llm", "enabled": False, "last_test_status": "failed",
        },
    ]
    ids = {
        **{scene: "qwen" for scene in DEMO_LLM_SCENES},
        "embedding": "bge",
        "speech_recognition": "sensevoice",
        "vision_understanding": "vision",
    }
    resolved = {
        "routes": [
            {
                "scene": scene,
                "model_kind": REQUIRED_MODEL_SCENES[scene][1],
                "model_config_id": ids.get(scene),
            }
            for scene in REQUIRED_MODEL_SCENES
        ]
    }
    policies = [{"enabled": True, "is_default": True, "routes": ids}]
    return models, resolved, policies


def test_model_route_audit_requires_tested_models_and_disabled_internal_qwen() -> None:
    models, resolved, policies = _routed_model_fixture()
    result = _model_route_audit(models, resolved, policies)
    assert result["ok"] is True
    assert result["internal_qwen_disabled"] is True
    assert set(result["core_routes"].values()) == {"qwen3.5-plus"}

    models[-1]["enabled"] = True
    failed = _model_route_audit(models, resolved, policies)
    assert failed["ok"] is False
    assert any("内网 Qwen" in message for message in failed["failures"])

    models[-1]["enabled"] = False
    next(row for row in models if row["id"] == "bge")["last_test_status"] = "failed"
    failed = _model_route_audit(models, resolved, policies)
    assert failed["ok"] is False
    assert any("向量化" in message for message in failed["failures"])


def test_report_secret_guard_fails_closed_without_false_positive_on_status() -> None:
    assert _report_has_secret({"api_key": "sk-do-not-print-1234567890"}) is True
    assert _report_has_secret({"detail": "Authorization: Bearer abc.def.ghi"}) is True
    assert _report_has_secret({"url": "https://demo:password@example.test/v1"}) is True
    assert _report_has_secret({"detail": "sk-abcdefghijklmnop"}) is True
    assert _report_has_secret({
        "api_key_status": "已配置",
        "models": {"status": "success"},
        "detail": "8 个业务场景已核验",
    }) is False


def test_formal_preflight_runs_live_model_tests_by_default() -> None:
    default = parser().parse_args([])
    assert default.skip_live_model_tests is False
    assert default.skip_database_ground_truth_tests is False
    assert default.skip_structured_api_tests is False
    assert default.skip_service_chain_tests is False
    skipped = parser().parse_args([
        "--skip-live-model-tests",
        "--skip-database-ground-truth-tests",
        "--skip-structured-api-tests",
        "--skip-service-chain-tests",
    ])
    assert skipped.skip_live_model_tests is True
    assert skipped.skip_database_ground_truth_tests is True
    assert skipped.skip_structured_api_tests is True
    assert skipped.skip_service_chain_tests is True


def test_source_sync_audit_requires_explicit_successful_unchanged_result() -> None:
    audited = _source_sync_audit([
        {"status": "succeeded", "result": {"unchanged": True}},
        {
            "status": "succeeded",
            "result": {"unchanged": False, "document_id": "d1", "version_id": "v1"},
        },
        {"status": "failed", "result": {"unchanged": True}},
    ])
    assert audited == {
        "completed": 2,
        "has_synchronized": True,
        "has_unchanged": True,
        "latest_completed_unchanged": True,
        "latest_status": "succeeded",
    }
    assert _source_sync_audit([
        {"status": "succeeded", "result": {"document_id": "d1"}},
    ])["has_unchanged"] is False
    assert _source_sync_audit([
        {"status": "succeeded", "result": {"unchanged": False, "document_id": "d2"}},
        {"status": "succeeded", "result": {"unchanged": True}},
    ])["latest_completed_unchanged"] is False


def test_sensitive_preview_contract_proves_server_side_blocking_and_masking() -> None:
    object_row = {
        "columns": [
            {"name": "demo_mobile", "sensitivity": "masked"},
            {"name": "demo_email", "sensitivity": "masked"},
            {"name": "demo_api_token", "sensitivity": "blocked", "sample_values": []},
        ],
    }
    preview = {
        "columns": [{"name": "demo_mobile"}, {"name": "demo_email"}],
        "rows": [{"demo_mobile": "138****0001", "demo_email": "d***@example.invalid"}],
    }
    ok, detail = _masked_preview_contract(object_row, preview)
    assert ok is True
    assert "禁止字段=通过" in detail

    leaked = {**preview, "rows": [{**preview["rows"][0], "demo_api_token": "forbidden"}]}
    assert _masked_preview_contract(object_row, leaked)[0] is False
    unmasked = {**preview, "rows": [{"demo_mobile": "13800000001", "demo_email": "demo@example.invalid"}]}
    assert _masked_preview_contract(object_row, unmasked)[0] is False


def test_empty_and_no_primary_key_preview_contracts_require_real_states() -> None:
    empty = {
        "mode": "live", "rows": [], "current_page_rows": 0, "has_next": False,
        "query_time": "2026-09-06T10:00:00+00:00", "elapsed_ms": 3,
    }
    assert _empty_preview_contract(empty) is True
    assert _empty_preview_contract({**empty, "rows": [{"id": 1}], "current_page_rows": 1}) is False

    no_primary = {"primary_key": []}
    warning = {
        "mode": "live", "query_time": "2026-09-06T10:00:00+00:00",
        "warnings": ["该对象没有主键，跨页查看时数据顺序可能随源库变化"],
    }
    assert _no_primary_key_preview_contract(no_primary, warning) is True
    assert _no_primary_key_preview_contract({"primary_key": ["id"]}, warning) is False


def test_structured_api_result_comparison_handles_numbers_rows_and_tolerance() -> None:
    assert _structured_rows_match(1700000, [{"total": "1700000.00"}], 0) is True
    assert _structured_rows_match(
        {"numerator": 1700000, "percent": 80.95238095},
        [{"numerator": "1700000", "percent": "80.95"}],
        {"numerator": 0, "percent": 0.01},
    ) is True
    assert _structured_rows_match(
        [{"name": "数字科技公司", "amount": 600000}],
        [{"name": "数字科技公司", "amount": "600000.0"}],
        {"amount": 0},
    ) is True
    assert _structured_rows_match(
        [{"org_unit": "数字科技公司", "amount": 600000}],
        [{"organization_name": "数字科技公司", "total_amount": "600000.0"}],
        {"amount": 0},
        require_field_names=False,
    ) is True
    assert _structured_rows_match(
        [{"org_unit": "数字科技公司", "amount": 600000}],
        [{"organization_name": "数字科技公司", "total_amount": "600000.0"}],
        {"amount": 0},
    ) is False
    assert _structured_rows_match(1700000, [{"total": 1790000}], 0) is False


def test_search_contract_requires_ranked_results_real_channel_and_trace() -> None:
    payload = {
        "query_id": "q1",
        "items": [
            {"rank": 1, "fused_score": 0.9, "channels": ["keyword", "vector"]},
            {"rank": 2, "fused_score": 0.8, "channels": ["keyword"]},
        ],
        "channel_counts": {"keyword": 2, "vector": 1, "graph": 1},
        "trace_summary": {
            "rrf_applied": True,
            "reranker_applied": False,
            "evidence_insufficient": False,
        },
    }
    assert _search_result_contract(payload, {"keyword", "vector", "graph"}) is True
    assert _search_result_contract(payload, {"keyword"}) is False
    assert _search_result_contract(
        {**payload, "items": list(reversed(payload["items"]))},
        {"keyword", "vector", "graph"},
    ) is False


def test_search_contract_accepts_only_visible_reranker_degradation() -> None:
    payload = {
        "query_id": "q1",
        "items": [{"rank": 1, "fused_score": 0.9, "channels": ["keyword", "graph"]}],
        "channel_counts": {"keyword": 2, "vector": 1, "graph": 1},
        "warnings": ["未配置默认重排模型，已使用 RRF 融合排序"],
        "trace_summary": {
            "rrf_applied": True,
            "reranker_applied": False,
            "evidence_insufficient": False,
        },
    }
    assert _search_result_contract(
        payload,
        {"keyword", "vector", "graph"},
        reranker_requested=True,
    ) is True
    assert _search_result_contract(
        {**payload, "warnings": []},
        {"keyword", "vector", "graph"},
        reranker_requested=True,
    ) is False
    applied = {
        **payload,
        "warnings": [],
        "trace_summary": {**payload["trace_summary"], "reranker_applied": True},
    }
    assert _search_result_contract(
        applied,
        {"keyword", "vector", "graph"},
        reranker_requested=True,
    ) is True


def test_formal_search_matrix_covers_all_six_demo_modes() -> None:
    assert list(SEARCH_MODE_CASES) == [
        "keyword", "vector", "graph", "keyword_vector", "hybrid", "hybrid_rerank",
    ]
    assert SEARCH_MODE_CASES["keyword_vector"]["channels"] == {"keyword", "vector"}
    assert SEARCH_MODE_CASES["hybrid_rerank"]["use_reranker"] is True


def test_structured_api_ground_truth_requires_plan_ir_execution_citation_and_objects() -> None:
    class FakeApi:
        def call(self, method, path, **kwargs):
            assert (method, path) == ("POST", "/structured-query/natural-language")
            assert kwargs["json"]["mapping_version_id"] == "mapping-v1"
            return {
                "plan": {"intent": "汇总有效采购金额"},
                "query_ir": {"from_entity": {"entity_id": "purchase-order"}},
                "compiled": {
                    "validation": {"ok": True},
                    "dialect": "postgresql",
                    "mapping_version_id": "mapping-v1",
                    "schema_version_id": "schema-v1",
                    "query_fingerprint": "query-fingerprint",
                    "sql_template": "SELECT sum(amount) FROM purchase_orders WHERE status = %(value)s",
                    "parameter_summary": [{"name": "value", "type": "string"}],
                    "referenced_objects": ["public.purchase_orders", "public.approval_records"],
                },
                "result": {
                    "status": "succeeded",
                    "query_run_id": "run-1",
                    "rows": [{"total": "1700000.00"}],
                    "validation": {"plan_fingerprint": "p", "ir_fingerprint": "i"},
                    "source_citations": [{"label": "经营数据"}],
                },
            }

    result = _verify_structured_api_ground_truth(
        FakeApi(),
        mapping_version_id="mapping-v1",
        questions=[{
            "question_id": "SQL-DEMO-001",
            "question": "有效采购总额是多少？",
            "expected_value": 1700000,
            "tolerance": 0,
            "expected_citations": [{"objects": ["purchase_orders", "approval_records"]}],
        }],
    )
    assert result == {
        "total": 1,
        "passed": 1,
        "mismatched": [],
        "request_failed": [],
        "diagnostics": [],
    }


def test_preflight_wrapper_requires_database_credential_unless_explicitly_skipped() -> None:
    shell = Path("scripts/demo/preflight_guolian_demo.sh").read_text(encoding="utf-8")
    assert "GUOLIAN_DEMO_DATABASE_PASSWORD" in shell
    assert "STRUCTURED_FIXTURE_PASSWORD" in shell
    assert "--skip-database-ground-truth-tests" in shell
    assert 'umask 077' in shell
