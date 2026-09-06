#!/usr/bin/env python3
"""Verify the Guolian demo through real, non-destructive platform calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (
    DEMO_DOCUMENT_NAMES,
    DEMO_NOTICE,
    DEMO_ROOT,
    DATABASE_TABLES,
    DEMO_LLM_SCENES,
    ONTOLOGY_CODE,
    SPACE_CODE,
    DemoError,
    SafeApiClient,
    _iter_items,
    admin_credentials_from_environment,
    api_url_from_environment,
    find_by,
    normalized_entity_name,
    normalized_evidence_text,
    paginated_items,
    redact,
    seeded_graph_ground_truth,
)
from scripts.demo.verify_guolian_ground_truth import (
    DatabaseVerificationError,
    load_and_validate_bundle,
    verify_database_ground_truth,
)
from scripts.demo.prepare_guolian_governance import GovernanceDemoPreparer


REQUIRED_MODEL_SCENES: dict[str, tuple[str, str, bool]] = {
    "agent_chat": ("智能问答", "llm", True),
    "semantic_extract": ("语义抽取", "llm", True),
    "document_governance": ("文档治理", "llm", True),
    "structured_query": ("结构化查询规划", "llm", True),
    "embedding": ("向量化", "embedding", True),
    "speech_recognition": ("语音识别", "asr", True),
    "vision_understanding": ("视觉理解", "vision", True),
    "reranking": ("检索重排", "reranker", False),
}

ACCOUNT_FREE_SOURCE_TYPES: frozenset[str] = frozenset({
    "web", "rest", "rss", "sitemap", "git", "s3", "local_dir",
})
ACCOUNT_FREE_SOURCE_NAMES: dict[str, str] = {
    "web": "演示·集团项目运行门户",
    "rest": "演示·供应商风险 REST API",
    "rss": "演示·采购与风险动态 RSS",
    "sitemap": "演示·集团知识站点 Sitemap",
    "git": "演示·项目配置 Git 仓库",
    "s3": "演示·MinIO 项目对象库",
    "local_dir": "演示·共享目录知识投递",
}
SENSITIVE_PREVIEW_OBJECT = "supplier_contacts"
EMPTY_PREVIEW_OBJECT = "archived_projects"
NO_PRIMARY_KEY_OBJECT = "procurement_order_overview"
EXPECTED_STRUCTURED_GROUND_TRUTH_CHECKS = 20
EXPECTED_DEMO_DOCUMENTS = 24
EXPECTED_SEEDED_GRAPH_FACTS = 9
EXPECTED_INFERENCE_PREMISE_FACTS = 8

SEARCH_MODE_CASES: dict[str, dict[str, Any]] = {
    "keyword": {
        "label": "仅全文检索",
        "channels": frozenset({"keyword"}),
        "flags": (True, False, False),
        "use_reranker": False,
    },
    "vector": {
        "label": "仅向量检索",
        "channels": frozenset({"vector"}),
        "flags": (False, True, False),
        "use_reranker": False,
    },
    "graph": {
        "label": "仅图谱检索",
        "channels": frozenset({"graph"}),
        "flags": (False, False, True),
        "use_reranker": False,
    },
    "keyword_vector": {
        "label": "全文＋向量检索",
        "channels": frozenset({"keyword", "vector"}),
        "flags": (True, True, False),
        "use_reranker": False,
    },
    "hybrid": {
        "label": "全文＋向量＋图谱",
        "channels": frozenset({"keyword", "vector", "graph"}),
        "flags": (True, True, True),
        "use_reranker": False,
    },
    "hybrid_rerank": {
        "label": "三路检索＋请求重排",
        "channels": frozenset({"keyword", "vector", "graph"}),
        "flags": (True, True, True),
        "use_reranker": True,
    },
}

_SECRET_FIELD = re.compile(
    r"(?i)(?:^|[_-])(api[_-]?key|password|passwd|secret|access[_-]?token|"
    r"authorization|credential|private[_-]?key)(?:$|[_-])"
)
_SECRET_TEXT = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]{8,}=*"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"://[^:/\s]+:[^@/\s]+@"),
    re.compile(
        r"(?i)[\"']?(?:api[_-]?key|password|passwd|secret|access[_-]?token|"
        r"authorization|private[_-]?key)[\"']?\s*[:=]\s*[\"']?(?!\*{3}|已配置|未配置)[^,}\]\s\"']+"
    ),
)


def _current_version(document_detail: dict[str, Any]) -> dict[str, Any] | None:
    current_id = document_detail.get("current_version_id")
    return next(
        (row for row in document_detail.get("versions") or [] if row.get("id") == current_id),
        None,
    )


def _fixture_checksum_audit(
    versions_by_title: dict[str, dict[str, Any] | None],
    *,
    demo_root: Path = DEMO_ROOT,
) -> tuple[list[str], list[str]]:
    """Prove that current platform versions match the checked-in fixtures.

    A title match alone is insufficient: an earlier demo retained an obsolete
    image under the expected title.  Only SHA-256 digests are returned, so this
    audit cannot leak the fixture contents.
    """

    matched: list[str] = []
    mismatched: list[str] = []
    for title in DEMO_DOCUMENT_NAMES:
        path = demo_root / title
        actual = str((versions_by_title.get(title) or {}).get("sha256") or "").casefold()
        expected = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        (matched if expected and actual == expected else mismatched).append(title)
    return matched, mismatched


def _release_counts(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {"graphs": 0, "indexes": 0, "knowledge": 0}
    return {
        key: sum(
            1 for row in (payload.get(key) or [])
            if isinstance(row, dict) and row.get("status") == "published"
        )
        for key in ("graphs", "indexes", "knowledge")
    }


def _markdown_was_parsed_as_text(
    version: dict[str, Any] | None,
    elements: list[dict[str, Any]],
) -> tuple[bool, str]:
    summary = (version or {}).get("parse_summary") or {}
    parser_name = str(summary.get("parser") or "").casefold()
    content_type = str((version or {}).get("content_type") or "").casefold()
    element_types = {str(row.get("element_type") or "").casefold() for row in elements}
    ok = bool(elements) and parser_name in {"text", "markdown"}
    ok = ok and ("markdown" in content_type or str((version or {}).get("filename") or "").casefold().endswith(".md"))
    ok = ok and not any("ocr" in value for value in element_types)
    return ok, f"parser={parser_name or 'missing'} / elements={len(elements)}"


def _timed_rows(rows: list[dict[str, Any]], *, start: str, end: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        begin = row.get(start)
        finish = row.get(end)
        if isinstance(begin, (int, float)) and isinstance(finish, (int, float)) and 0 <= begin <= finish:
            result.append(row)
    return result


def _source_sync_audit(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize real connector jobs without copying connector content.

    A source only proves incremental behaviour when a *successful* completed
    job explicitly reports ``unchanged=true``.  Merely having a source row or
    ``last_sync_status`` is not enough evidence.
    """

    completed = [
        row for row in jobs
        if row.get("status") == "succeeded" and isinstance(row.get("result"), dict)
    ]
    unchanged = [row for row in completed if row["result"].get("unchanged") is True]
    synchronized = [
        row for row in completed
        if row["result"].get("unchanged") is False
        or bool(row["result"].get("document_id") or row["result"].get("version_id"))
    ]
    return {
        "completed": len(completed),
        "has_synchronized": bool(synchronized),
        "has_unchanged": bool(unchanged),
        "latest_completed_unchanged": bool(
            completed and completed[0]["result"].get("unchanged") is True
        ),
        "latest_status": jobs[0].get("status") if jobs else None,
    }


def _masked_preview_contract(
    object_row: dict[str, Any],
    preview: dict[str, Any],
) -> tuple[bool, str]:
    """Validate server-side blocking/masking without returning source values."""

    discovered = {
        str(column.get("name")): column
        for column in (object_row.get("columns") or [])
        if column.get("name")
    }
    visible = {
        str(column.get("name"))
        for column in (preview.get("columns") or [])
        if column.get("name")
    }
    rows = [row for row in (preview.get("rows") or []) if isinstance(row, dict)]
    blocked = discovered.get("demo_api_token") or {}
    mobile = discovered.get("demo_mobile") or {}
    email = discovered.get("demo_email") or {}
    api_token_blocked = (
        blocked.get("sensitivity") == "blocked"
        and not (blocked.get("sample_values") or [])
        and "demo_api_token" not in visible
        and all("demo_api_token" not in row for row in rows)
    )
    mobile_masked = (
        mobile.get("sensitivity") == "masked"
        and "demo_mobile" in visible
        and bool(rows)
        and all(
            value is None or re.fullmatch(r"\d{0,3}\*{3,}\d{0,4}", str(value)) is not None
            for value in (row.get("demo_mobile") for row in rows)
        )
    )
    email_masked = (
        email.get("sensitivity") == "masked"
        and "demo_email" in visible
        and bool(rows)
        and all(
            value is None or re.fullmatch(r"[^@]{0,1}\*{3}@[^@]+", str(value)) is not None
            for value in (row.get("demo_email") for row in rows)
        )
    )
    ok = api_token_blocked and mobile_masked and email_masked
    return ok, (
        f"禁止字段={'通过' if api_token_blocked else '失败'} / "
        f"手机号脱敏={'通过' if mobile_masked else '失败'} / "
        f"邮箱脱敏={'通过' if email_masked else '失败'} / {len(rows)} 行"
    )


def _empty_preview_contract(preview: dict[str, Any]) -> bool:
    return bool(
        preview.get("mode") == "live"
        and preview.get("rows") == []
        and int(preview.get("current_page_rows") or 0) == 0
        and preview.get("has_next") is False
        and preview.get("query_time")
        and preview.get("elapsed_ms") is not None
    )


def _no_primary_key_preview_contract(
    object_row: dict[str, Any],
    preview: dict[str, Any],
) -> bool:
    warnings = [str(value) for value in (preview.get("warnings") or [])]
    return bool(
        not (object_row.get("primary_key") or [])
        and preview.get("mode") == "live"
        and preview.get("query_time")
        and any("没有主键" in warning for warning in warnings)
    )


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _ground_truth_values_match(expected: Any, actual: Any, tolerance: Any) -> bool:
    expected_number = _decimal_value(expected)
    actual_number = _decimal_value(actual)
    if expected_number is not None and actual_number is not None:
        allowed = _decimal_value(tolerance) or Decimal(0)
        return abs(expected_number - actual_number) <= allowed
    return expected == actual


def _structured_rows_match(
    expected: Any,
    rows: list[dict[str, Any]],
    tolerance: Any,
    *,
    require_field_names: bool = True,
) -> bool:
    """Compare a structured API response with the checked-in expectation."""

    if isinstance(expected, list):
        if len(expected) != len(rows):
            return False
        expected_rows = expected
    elif isinstance(expected, dict):
        if len(rows) != 1:
            return False
        expected_rows = [expected]
    else:
        if len(rows) != 1 or len(rows[0]) != 1:
            return False
        return _ground_truth_values_match(expected, next(iter(rows[0].values())), tolerance)
    for expected_row, actual_row in zip(expected_rows, rows, strict=True):
        if not isinstance(expected_row, dict) or len(expected_row) != len(actual_row):
            return False
        if require_field_names and set(expected_row) != set(actual_row):
            return False
        actual_values = (
            [actual_row[field] for field in expected_row]
            if require_field_names
            else list(actual_row.values())
        )
        for (field, expected_value), actual_value in zip(
            expected_row.items(), actual_values, strict=True,
        ):
            field_tolerance = tolerance.get(field, 0) if isinstance(tolerance, dict) else tolerance
            if not _ground_truth_values_match(expected_value, actual_value, field_tolerance):
                return False
    return True


def _database_url_for_ground_truth(source: dict[str, Any]) -> str:
    """Build a transient URL from safe config plus an environment-only secret."""

    password = (
        os.getenv("GUOLIAN_DEMO_DATABASE_PASSWORD", "").strip()
        or os.getenv("STRUCTURED_FIXTURE_PASSWORD", "").strip()
    )
    if not password:
        raise DatabaseVerificationError("未配置演示数据库核验凭据")
    config = source.get("config") or {}
    dialect = str(config.get("dialect") or "").casefold()
    if dialect not in {"postgresql", "mysql"}:
        raise DatabaseVerificationError("演示数据库方言不受支持")
    host = str(config.get("host") or "").strip()
    database = str(config.get("database") or "").strip()
    username = str(config.get("username") or "").strip()
    if not host or not database or not username:
        raise DatabaseVerificationError("演示数据库连接配置不完整")
    port = int(config.get("port") or (5432 if dialect == "postgresql" else 3306))
    driver = "postgresql+psycopg" if dialect == "postgresql" else "mysql+pymysql"
    return (
        f"{driver}://{quote_plus(username)}:{quote_plus(password)}@"
        f"{host}:{port}/{quote_plus(database)}"
    )


def _expected_citation_objects(question: dict[str, Any]) -> set[str]:
    return {
        str(name)
        for citation in (question.get("expected_citations") or [])
        for name in (citation.get("objects") or [])
    }


def _verify_structured_api_ground_truth(
    api: SafeApiClient,
    *,
    mapping_version_id: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run all declared questions through the real NL→Plan→IR→SQL API."""

    passed: list[str] = []
    mismatched: list[str] = []
    failed: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    for question in questions:
        question_id = str(question.get("question_id") or "unknown")
        try:
            response = api.call(
                "POST",
                "/structured-query/natural-language",
                json={
                    "mapping_version_id": mapping_version_id,
                    "question": question["question"],
                    "execute": True,
                    "max_rows": 500,
                },
            )
            result = response.get("result") or {}
            compiled = response.get("compiled") or {}
            rows = [row for row in (result.get("rows") or []) if isinstance(row, dict)]
            referenced = {
                str(value).rsplit(".", 1)[-1]
                for value in (compiled.get("referenced_objects") or [])
            }
            expected_objects = _expected_citation_objects(question)
            contract_components = {
                "plan": bool(response.get("plan")),
                "query_ir": bool(response.get("query_ir")),
                "compiled_validation": (compiled.get("validation") or {}).get("ok") is True,
                "dialect": compiled.get("dialect") in {"postgresql", "mysql"},
                "mapping_version": compiled.get("mapping_version_id") == mapping_version_id,
                "schema_version": bool(compiled.get("schema_version_id")),
                "query_fingerprint": bool(compiled.get("query_fingerprint")),
                "parameterized_sql": bool(compiled.get("sql_template")),
                "parameter_summary": isinstance(compiled.get("parameter_summary"), (dict, list)),
                "execution": result.get("status") == "succeeded",
                "query_run": bool(result.get("query_run_id")),
                "plan_fingerprint": bool((result.get("validation") or {}).get("plan_fingerprint")),
                "ir_fingerprint": bool((result.get("validation") or {}).get("ir_fingerprint")),
                "citation": bool(result.get("source_citations")),
                "citation_objects": expected_objects.issubset(referenced),
            }
            contract_ok = all(contract_components.values())
            values_ok = _structured_rows_match(
                question.get("expected_value"), rows, question.get("tolerance", 0),
                require_field_names=False,
            )
            if contract_ok and values_ok:
                passed.append(question_id)
            else:
                mismatched.append(question_id)
                diagnostics.append({
                    "question_id": question_id,
                    "kind": "mismatch",
                    "contract_failures": [name for name, ok in contract_components.items() if not ok],
                    "missing_citation_objects": sorted(expected_objects - referenced),
                    "value_match": values_ok,
                    "expected_shape": (
                        len(question.get("expected_value"))
                        if isinstance(question.get("expected_value"), list)
                        else 1
                    ),
                    "actual_shape": len(rows),
                    "actual_column_count": len(rows[0]) if rows else 0,
                })
        except Exception as exc:
            # Never include response bodies here: external providers and
            # connector errors must not leak credentials into preflight logs.
            failed.append(question_id)
            message = str(exc)
            http_status = re.search(r"HTTP\s+(\d{3})", message)
            error_code = re.search(r'"code"\s*:\s*"([A-Z0-9_]+)"', message)
            diagnostics.append({
                "question_id": question_id,
                "kind": "request_failed",
                "http_status": int(http_status.group(1)) if http_status else None,
                "error_code": error_code.group(1) if error_code else type(exc).__name__,
            })
    return {
        "total": len(questions),
        "passed": len(passed),
        "mismatched": mismatched,
        "request_failed": failed,
        "diagnostics": diagnostics,
    }


def _search_result_contract(
    payload: dict[str, Any],
    expected_channels: frozenset[str] | set[str],
    *,
    reranker_requested: bool = False,
) -> bool:
    """Validate real retrieval output for one explicit channel combination.

    The final RRF list is a union, so an individual result does not need to be
    present in every enabled channel.  The channel-level counts prove that
    each requested backend actually returned data, while item channels must
    remain a non-empty subset of the requested set.  A requested but
    unavailable reranker is an accepted *visible degradation* only when the
    backend returns a warning; silently pretending that reranking ran fails.
    """

    enabled = frozenset(expected_channels)
    items = [row for row in (payload.get("items") or []) if isinstance(row, dict)]
    counts = payload.get("channel_counts") or {}
    trace = payload.get("trace_summary") or {}
    warnings = [str(value) for value in (payload.get("warnings") or [])]
    ranks = [row.get("rank") for row in items]
    scores = [row.get("fused_score") for row in items]
    ranked = ranks == list(range(1, len(items) + 1))
    scores_valid = all(isinstance(value, (int, float)) for value in scores)
    sorted_scores = scores_valid and all(
        float(scores[index]) >= float(scores[index + 1])
        for index in range(len(scores) - 1)
    )
    channels_ok = bool(enabled) and all(int(counts.get(name, 0)) > 0 for name in enabled)
    channels_ok = channels_ok and all(
        int(counts.get(name, 0)) == 0
        for name in {"keyword", "vector", "graph"} - enabled
    )
    channels_ok = channels_ok and all(
        bool(set(row.get("channels") or []))
        and set(row.get("channels") or []).issubset(enabled)
        for row in items
    )
    reranker_applied = trace.get("reranker_applied") is True
    reranker_degraded = (
        trace.get("reranker_applied") is False
        and any("重排" in warning for warning in warnings)
    )
    reranker_ok = (
        reranker_applied or reranker_degraded
        if reranker_requested
        else trace.get("reranker_applied") is False
    )
    return bool(
        items
        and payload.get("query_id")
        and channels_ok
        and ranked
        and sorted_scores
        and trace.get("rrf_applied") is True
        and reranker_ok
        and trace.get("evidence_insufficient") is False
    )


def _graph_evidence_ground_truth_contract(
    specs: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    chunks_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Verify every seeded graph edge against its exact current source chunk.

    A graph edge merely carrying a chunk ID is not sufficient evidence.  The
    chunk must come from the declared fixture and structural position and its
    normalized text must contain the subject, predicate and object evidence
    terms.  The report intentionally contains only fact IDs and reason codes,
    never source text.
    """

    asserted = [
        row for row in facts
        if row.get("origin_type") in {None, "asserted"}
        and row.get("status") in {None, "published"}
    ]
    passed_ids: list[str] = []
    required_passed_ids: list[str] = []
    failures: list[dict[str, str]] = []

    for spec in specs:
        fact_id = str(spec["fact_id"])
        matches = [
            row for row in asserted
            if normalized_entity_name(row.get("subject_name"))
            == normalized_entity_name(spec["subject"])
            and str(row.get("predicate") or "").strip() == str(spec["predicate"])
            and normalized_entity_name(row.get("object_name"))
            == normalized_entity_name(spec["object"])
        ]
        if not matches:
            failures.append({
                "fact_id": fact_id,
                "reason": "fact_missing",
            })
            continue
        chunk_candidates: list[tuple[str, dict[str, Any]]] = []
        for match in matches:
            chunk_id = str(match.get("source_chunk_id") or "")
            chunk = chunks_by_id.get(chunk_id)
            if chunk_id and chunk:
                chunk_candidates.append((chunk_id, chunk))
        if not chunk_candidates:
            failures.append({"fact_id": fact_id, "reason": "source_chunk_missing"})
            continue
        document_candidates = [
            (chunk_id, chunk) for chunk_id, chunk in chunk_candidates
            if str(chunk.get("document_title") or "") == str(spec["source_title"])
        ]
        if not document_candidates:
            failures.append({"fact_id": fact_id, "reason": "source_document_mismatch"})
            continue
        path_candidates = [
            (chunk_id, chunk) for chunk_id, chunk in document_candidates
            if str(chunk.get("structural_path") or "") == str(spec["chunk_structural_path"])
        ]
        if not path_candidates:
            failures.append({"fact_id": fact_id, "reason": "structural_path_mismatch"})
            continue
        evidence_candidates_by_id: dict[str, dict[str, Any]] = {}
        for chunk_id, chunk in path_candidates:
            chunk_text = normalized_evidence_text(chunk.get("text"))
            if chunk_text and all(
                normalized_evidence_text(term) in chunk_text
                for term in spec.get("evidence_terms") or []
            ):
                evidence_candidates_by_id[chunk_id] = chunk
        evidence_candidates = list(evidence_candidates_by_id.values())
        if not evidence_candidates:
            failures.append({"fact_id": fact_id, "reason": "evidence_terms_missing"})
            continue
        if len(evidence_candidates) != 1:
            failures.append({"fact_id": fact_id, "reason": "evidence_ambiguous"})
            continue
        passed_ids.append(fact_id)
        if spec.get("required_for_inference") is True:
            required_passed_ids.append(fact_id)

    legacy_reverse_active = any(
        normalized_entity_name(row.get("subject_name"))
        == normalized_entity_name("智慧流程中枢项目")
        and str(row.get("predicate") or "").strip() == "负责"
        and normalized_entity_name(row.get("object_name"))
        == normalized_entity_name("数字科技公司")
        for row in asserted
    )
    if legacy_reverse_active:
        failures.append({
            "fact_id": "FACT-DEMO-008",
            "reason": "legacy_reverse_relation_active",
        })

    required_total = sum(row.get("required_for_inference") is True for row in specs)
    return {
        "total": len(specs),
        "passed": len(passed_ids),
        "passed_fact_ids": passed_ids,
        "required_total": required_total,
        "required_passed": len(required_passed_ids),
        "required_passed_fact_ids": required_passed_ids,
        "legacy_reverse_active": legacy_reverse_active,
        "failures": failures,
    }


def _job_target(row: dict[str, Any]) -> tuple[str, str]:
    payload = row.get("input") or {}
    target = next(
        (
            str(payload.get(key))
            for key in ("version_id", "source_id", "inference_run_id", "space_id")
            if payload.get(key)
        ),
        str(row.get("target_name") or row.get("id") or "unknown"),
    )
    return str(row.get("job_type") or "unknown"), target


def _latest_failed_jobs(
    rows: list[dict[str, Any]],
    *,
    current_version_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return unresolved failures while retaining the historical failure count.

    ``/jobs`` is newest-first.  A successful retry for the same durable target
    resolves an older failure, so only the newest job per type/target can block
    the live demo.  Historical failures remain visible in the report.
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    historical = 0
    for row in rows:
        if row.get("status") in {"failed", "partial_failed"}:
            historical += 1
        latest.setdefault(_job_target(row), row)
    unresolved = []
    for row in latest.values():
        if row.get("status") not in {"failed", "partial_failed"}:
            continue
        version_id = str((row.get("input") or {}).get("version_id") or "")
        # A failed historical version remains in the immutable job audit, but
        # it must not make the current knowledge space look broken after a new
        # version has been successfully published.  Current-version failures
        # still block the preflight.
        if current_version_ids is not None and version_id and version_id not in current_version_ids:
            continue
        unresolved.append(row)
    return unresolved, historical


def _report_has_secret(value: Any, *, key: str | None = None) -> bool:
    if key and _SECRET_FIELD.search(key) and not key.endswith("_status"):
        return True
    if isinstance(value, dict):
        return any(_report_has_secret(item, key=str(name)) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_report_has_secret(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) is not None for pattern in _SECRET_TEXT)
    return False


def _model_route_audit(
    models: list[dict[str, Any]],
    resolved_payload: dict[str, Any],
    policies: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {str(row.get("id")): row for row in models if row.get("id")}
    resolved = {
        str(row.get("scene")): row
        for row in (resolved_payload.get("routes") or [])
        if isinstance(row, dict) and row.get("scene")
    }
    failures: list[str] = []
    for scene, (label, expected_kind, required) in REQUIRED_MODEL_SCENES.items():
        route = resolved.get(scene)
        model = by_id.get(str((route or {}).get("model_config_id") or ""))
        if model is None:
            if required:
                failures.append(f"{label}未解析到模型")
            continue
        if not model.get("enabled"):
            failures.append(f"{label}引用停用模型")
        if model.get("model_kind") != expected_kind:
            failures.append(f"{label}模型类型错误")
        if model.get("last_test_status") != "success":
            failures.append(f"{label}模型连接测试未通过")
    routed_ids = {
        str(model_id)
        for policy in policies
        if policy.get("enabled") and policy.get("is_default")
        for model_id in (policy.get("routes") or {}).values()
        if model_id
    }
    for model_id in routed_ids:
        model = by_id.get(model_id)
        if model is None or not model.get("enabled") or model.get("last_test_status") != "success":
            failures.append("生效路由引用不可用模型")
    internal = [row for row in models if row.get("model_name") == "Qwen3.8-27B-NVFP4"]
    if any(row.get("enabled") or str(row.get("id")) in routed_ids for row in internal):
        failures.append("当前不可达的内网 Qwen 仍处于启用或路由状态")
    core_routes = {
        scene: (by_id.get(str((resolved.get(scene) or {}).get("model_config_id") or "")) or {}).get("model_name")
        for scene in DEMO_LLM_SCENES
    }
    if any(value != "qwen3.5-plus" for value in core_routes.values()):
        failures.append("主演示 LLM 场景未全部路由到 qwen3.5-plus")
    return {
        "ok": not failures,
        "failures": list(dict.fromkeys(failures)),
        "resolved_count": sum(bool(row.get("model_config_id")) for row in resolved.values()),
        "core_routes": core_routes,
        "internal_qwen_disabled": not any(row.get("enabled") for row in internal),
    }


def _check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": required, "detail": detail}


def verify(
    api: SafeApiClient,
    *,
    live_model_tests: bool = True,
    database_ground_truth_tests: bool = True,
    structured_api_tests: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    fact_ground_truth, structured_ground_truth = load_and_validate_bundle(
        DEMO_ROOT / "demo_ground_truth.json",
        DEMO_ROOT / "structured_query_ground_truth.json",
    )
    structured_questions = list(structured_ground_truth.get("questions") or [])
    checks.append(
        _check(
            "演示 Ground Truth",
            len(structured_questions) == EXPECTED_STRUCTURED_GROUND_TRUTH_CHECKS
            and bool(fact_ground_truth.get("facts"))
            and bool(fact_ground_truth.get("questions")),
            f"{len(fact_ground_truth.get('facts') or [])} 条知识事实 / "
            f"{len(structured_questions)}/{EXPECTED_STRUCTURED_GROUND_TRUTH_CHECKS} 条结构化问题",
        )
    )
    spaces = api.call("GET", "/spaces")
    space = find_by(spaces, "code", SPACE_CODE)
    checks.append(_check("演示空间", bool(space), "已创建" if space else "不存在"))
    if not space:
        return {"ready": False, "space_id": None, "checks": checks}
    space_id = space["id"]

    models = api.call("GET", "/model-configs")
    resolved_routes = api.call("GET", "/model-routing-policies/resolved")
    routing_policies = api.call("GET", "/model-routing-policies")
    model_test_results: dict[str, dict[str, Any]] = {}
    if live_model_tests:
        test_ids = {
            str(row.get("model_config_id"))
            for row in (resolved_routes.get("routes") or [])
            if row.get("model_config_id")
        }
        test_ids.update(
            str(row["id"])
            for row in models
            if row.get("id") and row.get("enabled")
            and (row.get("provider") == "kimi" or "kimi" in str(row.get("name") or "").casefold())
        )
        for model_id in sorted(test_ids):
            try:
                tested = api.call("POST", f"/model-configs/{model_id}/test")
                model_test_results[model_id] = {
                    "status": tested.get("status"),
                    "elapsed_ms": tested.get("elapsed_ms"),
                }
            except Exception as exc:
                model_test_results[model_id] = {
                    "status": "request_failed",
                    "error_type": type(exc).__name__,
                }
        # The endpoint persists the real result. Refresh before auditing routes.
        models = api.call("GET", "/model-configs")

    for kind, label, required in (
        ("llm", "默认大模型", True),
        ("embedding", "向量模型", True),
        ("asr", "语音识别模型", True),
        ("vision", "视觉模型", True),
        ("reranker", "重排模型", False),
    ):
        candidates = [row for row in models if row.get("model_kind") == kind and row.get("enabled")]
        selected = next((row for row in candidates if row.get("is_default")), candidates[0] if candidates else None)
        ok = bool(selected and selected.get("last_test_status") == "success")
        detail = (
            f"{selected.get('name')} / {selected.get('last_test_status') or '尚未测试'}"
            if selected else "未配置"
        )
        checks.append(_check(label, ok, detail, required=required))

    route_audit = _model_route_audit(models, resolved_routes, routing_policies)
    checks.append(
        _check(
            "模型路由",
            route_audit["ok"],
            "8 个业务场景已核验" if route_audit["ok"] else "；".join(route_audit["failures"][:4]),
        )
    )
    extraction_policies = api.call("GET", "/extraction-policies")
    extraction_policy = next(
        (row for row in extraction_policies if row.get("enabled") and row.get("is_default")),
        next((row for row in extraction_policies if row.get("enabled")), None),
    )
    models_by_id = {str(row.get("id")): row for row in models if row.get("id")}
    extraction_model = models_by_id.get(str((extraction_policy or {}).get("model_config_id") or ""))
    extraction_ok = bool(
        extraction_policy
        and extraction_model
        and extraction_model.get("enabled")
        and extraction_model.get("last_test_status") == "success"
        and extraction_model.get("model_name") == "qwen3.5-plus"
    )
    checks.append(
        _check(
            "语义抽取显式策略",
            extraction_ok,
            f"{(extraction_policy or {}).get('name') or '未配置'} / "
            f"{(extraction_model or {}).get('name') or '未关联可用模型'}",
        )
    )
    kimi_models = [
        row for row in models
        if row.get("provider") == "kimi" or "kimi" in str(row.get("name") or "").casefold()
    ]
    kimi_usable = [
        row for row in kimi_models
        if row.get("enabled") and row.get("last_test_status") == "success"
    ]
    checks.append(
        _check(
            "Kimi 备用模型",
            bool(kimi_usable),
            f"{len(kimi_usable)}/{len(kimi_models)} 个当前可用",
            required=False,
        )
    )

    documents = api.call("GET", f"/documents?space_id={space_id}")
    by_title = {row["title"]: row for row in documents}
    required_titles = list(DEMO_DOCUMENT_NAMES)
    missing = sorted(set(required_titles) - set(by_title))
    duplicate_titles = sorted({
        title for title in required_titles
        if sum(row.get("title") == title for row in documents) > 1
    })
    checks.append(
        _check(
            "主演示文档",
            len(required_titles) == EXPECTED_DEMO_DOCUMENTS and not missing and not duplicate_titles,
            f"{len(required_titles) - len(missing)}/{EXPECTED_DEMO_DOCUMENTS} 份齐备；"
            f"缺少 {len(missing)} 份；重复标题 {len(duplicate_titles)} 个；空间共 {len(documents)} 份",
        )
    )

    details_by_title: dict[str, dict[str, Any]] = {}
    versions_by_title: dict[str, dict[str, Any] | None] = {}
    elements_by_title: dict[str, list[dict[str, Any]]] = {}
    chunks_by_id: dict[str, dict[str, Any]] = {}
    for title in required_titles:
        document = by_title.get(title)
        if not document:
            continue
        detail = api.call("GET", f"/documents/{document['id']}")
        details_by_title[title] = detail
        current = _current_version(detail)
        versions_by_title[title] = current
        if current:
            elements_by_title[title] = _iter_items(
                api.call("GET", f"/versions/{current['id']}/elements?limit=500")
            )
            for chunk in _iter_items(
                api.call("GET", f"/versions/{current['id']}/chunks?limit=500")
            ):
                chunk_id = str(chunk.get("id") or "")
                if chunk_id:
                    chunks_by_id[chunk_id] = {
                        **chunk,
                        "document_title": title,
                        "document_id": document["id"],
                        "version_id": current["id"],
                    }

    matching_fixtures, stale_fixtures = _fixture_checksum_audit(versions_by_title)
    checks.append(
        _check(
            "演示素材版本一致性",
            len(matching_fixtures) == len(required_titles) and not stale_fixtures,
            f"{len(matching_fixtures)}/{len(required_titles)} 份当前版本与交付素材 SHA-256 一致；"
            f"不一致 {len(stale_fixtures)} 份",
        )
    )

    published = 0
    failed_documents: list[str] = []
    for title in required_titles:
        current = versions_by_title.get(title)
        if ((current or {}).get("parse_summary") or {}).get("knowledge_status") == "published":
            published += 1
        else:
            failed_documents.append(title)
    checks.append(
        _check(
            "知识加工",
            bool(required_titles) and published == len(required_titles),
            f"{published}/{len(required_titles)} 份主演示材料已发布",
        )
    )

    profiles = [detail.get("profile") for detail in details_by_title.values() if detail.get("profile")]
    rich_profiles = [
        row for row in profiles
        if str(row.get("summary") or "").strip()
        and str(row.get("classification") or "").strip()
        and row.get("quality_score") is not None
    ]
    model_profiles = [row for row in profiles if row.get("model_status") == "succeeded"]
    checks.append(
        _check(
            "治理画像",
            len(rich_profiles) == len(required_titles) and bool(model_profiles),
            f"{len(rich_profiles)}/{len(required_titles)} 完整；{len(model_profiles)} 份模型治理成功",
        )
    )

    markdown_results = [
        _markdown_was_parsed_as_text(versions_by_title.get(title), elements_by_title.get(title, []))
        for title in required_titles if title.casefold().endswith(".md")
    ]
    checks.append(
        _check(
            "Markdown 未进入 OCR",
            bool(markdown_results) and all(item[0] for item in markdown_results),
            "；".join(item[1] for item in markdown_results) or "未找到 Markdown",
        )
    )

    parser_expectations = {
        "智慧流程中枢项目周报（演示版）.docx": "docx",
        "集团知识底座建设汇报（演示版）.pptx": "pptx",
        "供应商风险台账（演示版）.xlsx": "excel",
        "采购订单明细（演示版）.csv": "csv",
        "项目系统依赖关系（演示版）.json": "json",
    }
    parser_matches = sum(
        str(((versions_by_title.get(title) or {}).get("parse_summary") or {}).get("parser") or "").casefold()
        == expected
        for title, expected in parser_expectations.items()
    )
    checks.append(
        _check(
            "办公与结构化文件解析路由",
            parser_matches == len(parser_expectations),
            f"{parser_matches}/{len(parser_expectations)} 个文件使用预期真实解析器",
        )
    )

    email_title = "东方智造交付延期通知（演示版）.eml"
    email_elements = elements_by_title.get(email_title, [])
    email_types = {str(row.get("element_type") or "") for row in email_elements}
    email_parser = str(
        ((versions_by_title.get(email_title) or {}).get("parse_summary") or {}).get("parser") or ""
    ).casefold()
    email_attachment_linked = any(
        bool((row.get("metadata") or row.get("element_metadata") or {}).get("attachment_parent"))
        for row in email_elements
    )
    checks.append(
        _check(
            "邮件正文与附件",
            email_parser == "email"
            and {"email", "attachment"}.issubset(email_types)
            and email_attachment_linked,
            f"parser={email_parser or 'missing'} / {len(email_elements)} 个元素 / "
            f"附件父级={'完整' if email_attachment_linked else '缺失'}",
        )
    )

    archive_title = "国联集团演示知识包.zip"
    archive_elements = elements_by_title.get(archive_title, [])
    archive_types = {str(row.get("element_type") or "") for row in archive_elements}
    archive_parser = str(
        ((versions_by_title.get(archive_title) or {}).get("parse_summary") or {}).get("parser") or ""
    ).casefold()
    archive_members = sum(
        bool((row.get("metadata") or row.get("element_metadata") or {}).get("archive_member"))
        for row in archive_elements
    )
    checks.append(
        _check(
            "ZIP 安全递归解析",
            archive_parser == "safe-zip"
            and archive_members > 0
            and len(archive_types) >= 3,
            f"parser={archive_parser or 'missing'} / {archive_members} 个可追溯归档元素 / "
            f"{len(archive_types)} 种元素类型",
        )
    )

    pdf_titles = [
        "智慧流程中枢项目建设方案（演示版）.pdf",
        "供应商现场评估记录（演示版）-扫描件.pdf",
    ]
    located_pdfs = {
        title: [row for row in elements_by_title.get(title, []) if row.get("page_number") is not None]
        for title in pdf_titles
    }
    scan_parser = str(
        ((versions_by_title.get(pdf_titles[1]) or {}).get("parse_summary") or {}).get("parser") or ""
    ).casefold()
    checks.append(
        _check(
            "PDF 页码与扫描件 OCR",
            all(located_pdfs.get(title) for title in pdf_titles) and scan_parser == "docling",
            f"普通 PDF {len(located_pdfs[pdf_titles[0]])} 个页码元素 / "
            f"扫描 PDF {len(located_pdfs[pdf_titles[1]])} 个页码元素 / parser={scan_parser or 'missing'}",
        )
    )

    audio = by_title.get("智慧流程中枢项目例会（演示版）.wav")
    audio_segments = api.call("GET", f"/documents/{audio['id']}/transcript") if audio else []
    timed_audio_segments = _timed_rows(audio_segments, start="time_start", end="time_end")
    audio_profile = api.call("GET", f"/documents/{audio['id']}/media-profile") if audio else {}
    checks.append(
        _check(
            "音频 ASR 与时间戳",
            bool(audio_segments)
            and len(timed_audio_segments) == len(audio_segments)
            and all(str(row.get("text") or "").strip() for row in audio_segments)
            and (audio_profile.get("run") or {}).get("status") in {"succeeded", "partial"}
            and bool((audio_profile.get("models") or {}).get("asr")),
            f"{len(audio_segments)} 个带文本和时间区间的转写片段",
        )
    )
    video = by_title.get("智慧流程中枢项目介绍（演示版）.mp4")
    video_profile = api.call("GET", f"/documents/{video['id']}/media-profile") if video else {}
    video_frames = api.call("GET", f"/documents/{video['id']}/frames") if video else []
    video_timeline = api.call("GET", f"/documents/{video['id']}/timeline") if video else []
    video_transcript = api.call("GET", f"/documents/{video['id']}/transcript") if video else []
    described_frames = [
        row for row in video_frames
        if str((row.get("vision_result") or {}).get("summary") or "").strip()
    ]
    timed_frames = [
        row for row in video_frames
        if isinstance(row.get("timestamp_seconds"), (int, float)) and row["timestamp_seconds"] >= 0
    ]
    timed_video_transcript = _timed_rows(video_transcript, start="time_start", end="time_end")
    timed_timeline = _timed_rows(video_timeline, start="time_start", end="time_end")
    checks.append(
        _check(
            "视频音轨与抽帧",
            (video_profile.get("run") or {}).get("status") in {"succeeded", "partial"}
            and bool(video_frames)
            and len(timed_frames) == len(video_frames)
            and bool(video_transcript)
            and len(timed_video_transcript) == len(video_transcript)
            and bool(timed_timeline),
            f"{len(video_frames)} 个带时间点的关键帧 / {len(video_transcript)} 个带时间区间的转写片段 / "
            f"{len(timed_timeline)} 条可定位时间线",
        )
    )
    checks.append(
        _check(
            "视频视觉理解",
            bool(video_frames)
            and len(described_frames) == len(video_frames)
            and bool((video_profile.get("models") or {}).get("vision")),
            f"{len(described_frames)}/{len(video_frames)} 帧有视觉描述",
        )
    )

    image_titles = (
        "项目总体架构图（演示版）.png",
        "扫描采购审批单（演示版）.jpg",
        "供应商评估表截图（演示版）.png",
    )
    image_results: list[tuple[bool, int, int]] = []
    for image_title in image_titles:
        image = by_title.get(image_title)
        image_profile = api.call("GET", f"/documents/{image['id']}/media-profile") if image else {}
        image_frames = api.call("GET", f"/documents/{image['id']}/frames") if image else []
        image_described = [
            row for row in image_frames
            if str((row.get("vision_result") or {}).get("summary") or "").strip()
        ]
        image_results.append((
            bool(
                image_frames
                and len(image_described) == len(image_frames)
                and bool((image_profile.get("models") or {}).get("vision"))
                and any(str(row.get("ocr_text") or "").strip() for row in image_frames)
            ),
            len(image_frames),
            len(image_described),
        ))
    checks.append(
        _check(
            "图片 OCR 与视觉理解",
            len(image_results) == 3 and all(item[0] for item in image_results),
            f"{sum(item[0] for item in image_results)}/3 份图片完成 OCR 与视觉理解 / "
            f"{sum(item[1] for item in image_results)} 帧 / "
            f"{sum(item[2] for item in image_results)} 帧有视觉描述",
        )
    )

    sources = api.call("GET", f"/sources?space_id={space_id}")
    account_free_sources = [
        row for row in sources if str(row.get("source_type") or "") in ACCOUNT_FREE_SOURCE_TYPES
    ]
    account_free_by_type = {
        source_type: [
            row for row in account_free_sources
            if row.get("source_type") == source_type
            and row.get("name") == ACCOUNT_FREE_SOURCE_NAMES[source_type]
        ]
        for source_type in ACCOUNT_FREE_SOURCE_TYPES
    }
    account_free_connections = 0
    account_free_documents = 0
    account_free_unchanged = 0
    for source_type in sorted(ACCOUNT_FREE_SOURCE_TYPES):
        candidates = account_free_by_type[source_type]
        if len(candidates) != 1:
            continue
        source = candidates[0]
        try:
            tested = api.call(
                "POST", "/sources/test",
                json={
                    "source_id": source["id"],
                    "source_type": source["source_type"],
                    "config": source.get("config") or {},
                },
            )
            if tested.get("status") == "success" and int(tested.get("bytes") or 0) > 0:
                account_free_connections += 1
        except Exception:
            pass
        source_documents = [row for row in documents if row.get("source_id") == source.get("id")]
        source_document_published = False
        for document in source_documents:
            try:
                detail = api.call("GET", f"/documents/{document['id']}")
                current = _current_version(detail)
                if ((current or {}).get("parse_summary") or {}).get("knowledge_status") == "published":
                    source_document_published = True
                    break
            except Exception:
                continue
        if source_document_published:
            account_free_documents += 1
        try:
            jobs_payload = api.call("GET", f"/sources/{source['id']}/jobs?limit=200")
            sync_audit = _source_sync_audit(_iter_items(jobs_payload))
            if sync_audit["has_unchanged"] and sync_audit["latest_completed_unchanged"]:
                account_free_unchanged += 1
        except Exception:
            pass
    checks.append(
        _check(
            "七类多源接入",
            len(account_free_sources) == 7
            and all(len(rows) == 1 for rows in account_free_by_type.values()),
            f"{len(account_free_sources)}/7 个；"
            f"类型 {', '.join(sorted(row.get('source_type') for row in account_free_sources)) or '无'}",
        )
    )
    checks.append(
        _check(
            "七类来源真实连接与加工",
            account_free_connections == 7 and account_free_documents == 7,
            f"连接 {account_free_connections}/7 / 已发布同步文档 {account_free_documents}/7",
        )
    )
    checks.append(
        _check(
            "七类来源增量 unchanged",
            account_free_unchanged == 7,
            f"{account_free_unchanged}/7 个来源有成功 unchanged 任务证据",
        )
    )

    database_sources = [row for row in sources if row.get("source_type") == "database"]
    dialects = {str((row.get("config") or {}).get("dialect") or "").casefold() for row in database_sources}
    checks.append(
        _check(
            "数据库数据源",
            len(database_sources) == 2 and dialects == {"postgresql", "mysql"},
            f"{len(database_sources)} 个 / {', '.join(sorted(dialects)) or '未识别方言'}",
        )
    )
    live_database_checks = 0
    schema_checks = 0
    preview_checks = 0
    sensitive_preview_checks = 0
    blocked_filter_checks = 0
    empty_preview_checks = 0
    no_primary_key_checks = 0
    source_schema_by_id: dict[str, dict[str, Any]] = {}
    database_objects_by_source: dict[str, list[dict[str, Any]]] = {}
    for source in database_sources:
        tested = api.call(
            "POST", "/sources/test",
            json={
                "source_id": source["id"],
                "source_type": source["source_type"],
                "config": source.get("config") or {},
            },
        )
        if tested.get("status") == "success":
            live_database_checks += 1
        schema_versions = _iter_items(api.call("GET", f"/sources/{source['id']}/schema/versions"))
        current_schema = next((row for row in schema_versions if row.get("status") == "current"), None)
        objects_payload = api.call("GET", f"/sources/{source['id']}/schema/objects") if current_schema else {}
        objects = objects_payload.get("objects") or []
        database_objects_by_source[str(source["id"])] = objects
        object_names = {str(row.get("name") or "") for row in objects}
        table_count = sum(row.get("kind") == "table" for row in objects)
        schema_ok = bool(
            current_schema
            and current_schema.get("schema_fingerprint")
            and int(current_schema.get("object_count") or 0) == len(objects)
            and table_count == len(DATABASE_TABLES)
            and int(current_schema.get("primary_key_count") or 0) > 0
            and int(current_schema.get("foreign_key_count") or 0) > 0
            and set(DATABASE_TABLES).issubset(object_names)
            and objects_payload.get("schema_version_id") == current_schema.get("id")
        )
        if schema_ok:
            schema_checks += 1
            source_schema_by_id[str(source["id"])] = current_schema
        suppliers = next((row for row in objects if row.get("name") == "suppliers"), None)
        if suppliers:
            preview = api.call(
                "POST",
                f"/sources/{source['id']}/data-preview",
                json={
                    "object_id": suppliers["id"],
                    "mode": "live",
                    "page": 1,
                    "page_size": 2,
                    "order_direction": "asc",
                    "filters": [],
                },
            )
            preview_ok = bool(
                preview.get("mode") == "live"
                and preview.get("schema_version_id") == current_schema.get("id")
                and preview.get("rows")
                and preview.get("query_time")
                and preview.get("elapsed_ms") is not None
            )
            if preview_ok:
                preview_checks += 1
        contacts = next((row for row in objects if row.get("name") == SENSITIVE_PREVIEW_OBJECT), None)
        if contacts:
            try:
                preview = api.call(
                    "POST", f"/sources/{source['id']}/data-preview",
                    json={
                        "object_id": contacts["id"], "mode": "live", "page": 1,
                        "page_size": 20, "order_direction": "asc", "filters": [],
                    },
                )
                if _masked_preview_contract(contacts, preview)[0]:
                    sensitive_preview_checks += 1
            except Exception:
                pass
            blocked_column = next(
                (row for row in (contacts.get("columns") or []) if row.get("name") == "demo_api_token"),
                None,
            )
            if blocked_column:
                try:
                    api.call(
                        "POST", f"/sources/{source['id']}/data-preview",
                        json={
                            "object_id": contacts["id"], "mode": "live", "page": 1,
                            "page_size": 20, "order_direction": "asc",
                            "filters": [{
                                "column_id": blocked_column["id"],
                                "operator": "eq", "value": "verification-probe",
                            }],
                        },
                    )
                except DemoError as exc:
                    if "HTTP 403" in str(exc):
                        blocked_filter_checks += 1
        empty_object = next((row for row in objects if row.get("name") == EMPTY_PREVIEW_OBJECT), None)
        if empty_object:
            try:
                preview = api.call(
                    "POST", f"/sources/{source['id']}/data-preview",
                    json={
                        "object_id": empty_object["id"], "mode": "live", "page": 1,
                        "page_size": 20, "order_direction": "asc", "filters": [],
                    },
                )
                if _empty_preview_contract(preview):
                    empty_preview_checks += 1
            except Exception:
                pass
        no_primary = next(
            (row for row in objects if row.get("name") == NO_PRIMARY_KEY_OBJECT),
            next((row for row in objects if not (row.get("primary_key") or [])), None),
        )
        if no_primary:
            try:
                preview = api.call(
                    "POST", f"/sources/{source['id']}/data-preview",
                    json={
                        "object_id": no_primary["id"], "mode": "live", "page": 1,
                        "page_size": 20, "order_direction": "asc", "filters": [],
                    },
                )
                if _no_primary_key_preview_contract(no_primary, preview):
                    no_primary_key_checks += 1
            except Exception:
                pass
    checks.append(_check("数据库实时连接", live_database_checks == len(database_sources) and live_database_checks >= 2, f"{live_database_checks}/{len(database_sources)} 可用"))
    checks.append(
        _check(
            "数据库 Schema",
            schema_checks == len(database_sources) and schema_checks >= 2,
            f"{schema_checks}/{len(database_sources)} 当前 Schema 完整；每库 {len(DATABASE_TABLES)} 张业务表",
        )
    )
    checks.append(_check("数据库实时预览", preview_checks == len(database_sources) and preview_checks >= 2, f"{preview_checks}/{len(database_sources)} 返回真实行"))
    checks.append(
        _check(
            "数据库敏感字段服务端保护",
            sensitive_preview_checks == len(database_sources)
            and blocked_filter_checks == len(database_sources)
            and sensitive_preview_checks >= 2,
            f"预览脱敏 {sensitive_preview_checks}/{len(database_sources)} / "
            f"禁止字段筛选拒绝 {blocked_filter_checks}/{len(database_sources)}",
        )
    )
    checks.append(
        _check(
            "数据库空表预览",
            empty_preview_checks == len(database_sources) and empty_preview_checks >= 2,
            f"{empty_preview_checks}/{len(database_sources)} 返回真实空状态",
        )
    )
    checks.append(
        _check(
            "数据库无主键对象",
            no_primary_key_checks == len(database_sources) and no_primary_key_checks >= 2,
            f"{no_primary_key_checks}/{len(database_sources)} 返回稳定性 Warning；"
            f"期望对象 {NO_PRIMARY_KEY_OBJECT}",
        )
    )
    mappings = _iter_items(api.call("GET", f"/semantic-mappings?space_id={space_id}"))
    active_mappings = [row for row in mappings if row.get("status") == "active" and row.get("active_version_id")]
    valid_active_mappings = []
    for mapping in active_mappings:
        detail = api.call("GET", f"/semantic-mappings/{mapping['id']}")
        version = detail.get("active_version") or {}
        source_schema = source_schema_by_id.get(str(detail.get("source_id") or ""))
        if (
            source_schema
            and version.get("id") == detail.get("active_version_id")
            and version.get("status") == "active"
            and version.get("schema_version_id") == source_schema.get("id")
            and version.get("schema_fingerprint") == source_schema.get("schema_fingerprint")
            and (version.get("validation_report") or {}).get("ok") is True
            and version.get("mapping_hash")
        ):
            valid_active_mappings.append(mapping)
    checks.append(_check("数据库语义映射", bool(valid_active_mappings), f"{len(valid_active_mappings)}/{len(active_mappings)} 个激活映射与当前 Schema 一致"))

    database_ground_truth: dict[str, Any] = {
        "executed": database_ground_truth_tests,
        "questions_per_database": len(structured_questions),
        "dialects": {},
    }
    if database_ground_truth_tests:
        for dialect in ("postgresql", "mysql"):
            source = next(
                (
                    row for row in database_sources
                    if str((row.get("config") or {}).get("dialect") or "").casefold() == dialect
                ),
                None,
            )
            passed = 0
            failed_ids: list[str] = []
            status = "not_configured"
            if source:
                try:
                    results = verify_database_ground_truth(
                        structured_ground_truth,
                        database_url=_database_url_for_ground_truth(source),
                    )
                    passed = sum(item.passed for item in results)
                    failed_ids = [item.question_id for item in results if not item.passed]
                    status = "completed"
                except Exception:
                    # Do not serialize exception messages: a driver may include
                    # connection metadata. The operator gets an explicit state
                    # while the secret guard remains fail-closed.
                    status = "request_failed"
            database_ground_truth["dialects"][dialect] = {
                "status": status,
                "passed": passed,
                "total": len(structured_questions),
                "failed_question_ids": failed_ids,
            }
        direct_passed = sum(
            int(row.get("passed") or 0)
            for row in database_ground_truth["dialects"].values()
        )
        direct_total = len(structured_questions) * 2
        checks.append(
            _check(
                "结构化 Ground Truth 直连核验",
                direct_passed == direct_total
                and direct_total == EXPECTED_STRUCTURED_GROUND_TRUTH_CHECKS * 2,
                f"MySQL + PostgreSQL 共 {direct_passed}/{direct_total} 条通过",
            )
        )
    else:
        checks.append(
            _check(
                "结构化 Ground Truth 直连核验",
                False,
                "本次显式跳过；不能据此宣称数据库 Ground Truth 已通过",
            )
        )

    structured_api_ground_truth: dict[str, Any] = {
        "executed": structured_api_tests,
        "total": len(structured_questions),
        "passed": 0,
        "mismatched": [],
        "request_failed": [],
    }
    mapping_for_api = valid_active_mappings[0] if valid_active_mappings else None
    if structured_api_tests and mapping_for_api:
        mapping_detail = api.call("GET", f"/semantic-mappings/{mapping_for_api['id']}")
        mapping_version_id = str(mapping_detail.get("active_version_id") or "")
        if mapping_version_id:
            structured_api_ground_truth = {
                "executed": True,
                **_verify_structured_api_ground_truth(
                    api,
                    mapping_version_id=mapping_version_id,
                    questions=structured_questions,
                ),
            }
        api_passed = int(structured_api_ground_truth.get("passed") or 0)
        checks.append(
            _check(
                "结构化语义查询 API Ground Truth",
                api_passed == EXPECTED_STRUCTURED_GROUND_TRUTH_CHECKS,
                f"NL→Plan→IR→参数化 SQL→执行 {api_passed}/"
                f"{len(structured_questions)} 条通过；结果不一致 "
                f"{len(structured_api_ground_truth.get('mismatched') or [])}；"
                f"请求失败 {len(structured_api_ground_truth.get('request_failed') or [])}",
            )
        )
    elif structured_api_tests:
        checks.append(
            _check(
                "结构化语义查询 API Ground Truth",
                False,
                "没有与当前 Schema 一致的激活映射，20 条 API 链路未执行",
            )
        )
    else:
        checks.append(
            _check(
                "结构化语义查询 API Ground Truth",
                False,
                "本次显式跳过；仅有直连核验不能证明语义查询链路可用",
            )
        )

    ontologies = api.call("GET", f"/ontologies?space_id={space_id}")
    ontology = find_by(ontologies, "code", ONTOLOGY_CODE)
    terms = api.call("GET", f"/ontologies/{ontology['id']}/terms") if ontology else []
    checks.append(_check("演示本体", bool(ontology) and len(terms) >= 20, f"{len(terms)} 个词条"))

    entities = paginated_items(api, f"/knowledge/entities?space_id={space_id}")
    facts = paginated_items(api, f"/knowledge/facts?space_id={space_id}")
    seeded_specs = seeded_graph_ground_truth(DEMO_ROOT)
    graph_evidence_ground_truth = _graph_evidence_ground_truth_contract(
        seeded_specs,
        facts,
        chunks_by_id,
    )
    graph_evidence_ok = bool(
        graph_evidence_ground_truth["total"] == EXPECTED_SEEDED_GRAPH_FACTS
        and graph_evidence_ground_truth["passed"] == EXPECTED_SEEDED_GRAPH_FACTS
        and graph_evidence_ground_truth["required_total"] == EXPECTED_INFERENCE_PREMISE_FACTS
        and graph_evidence_ground_truth["required_passed"] == EXPECTED_INFERENCE_PREMISE_FACTS
        and not graph_evidence_ground_truth["legacy_reverse_active"]
        and not graph_evidence_ground_truth["failures"]
    )
    checks.append(
        _check(
            "演示事实证据 Ground Truth",
            graph_evidence_ok,
            f"已声明事实 {graph_evidence_ground_truth['passed']}/{graph_evidence_ground_truth['total']} 条证据一致；"
            f"推演前提 {graph_evidence_ground_truth['required_passed']}/"
            f"{graph_evidence_ground_truth['required_total']} 条一致；"
            f"反向责任关系 {1 if graph_evidence_ground_truth['legacy_reverse_active'] else 0} 条；"
            f"失败 {len(graph_evidence_ground_truth['failures'])} 条",
        )
    )
    releases = api.call("GET", f"/knowledge/releases?space_id={space_id}")
    release_counts = _release_counts(releases)
    checks.append(_check("知识图谱", len(entities) >= 10 and len(facts) >= 8, f"{len(entities)} 节点 / {len(facts)} 关系"))
    checks.append(
        _check(
            "图谱与索引发布",
            all(release_counts[key] > 0 for key in ("graphs", "indexes", "knowledge")),
            f"图谱 {release_counts['graphs']} / 索引 {release_counts['indexes']} / 知识 {release_counts['knowledge']}",
        )
    )

    readiness = api.call("GET", f"/analysis/readiness?space_id={space_id}")
    tasks = [
        row for row in api.call("GET", "/analysis/tasks")
        if space_id in (row.get("space_ids") or [])
    ]
    checks.append(_check("知识分析数据准备", not readiness.get("blocking_issues"), f"{len(tasks)} 个分析任务"))
    successful_analysis = 0
    analysis_results = 0
    evidence_linked_results = 0
    expected_analysis_results = {
        "演示·制度适用范围": ("采购实施细则", "适用于", "数字科技公司"),
        "演示·供应商风险传导": ("智慧流程中枢项目", "受到影响", "交付延期"),
        "演示·系统依赖影响": ("智慧流程中枢", "受到影响", "升级维护事件"),
    }
    expected_analysis_hits = 0
    for task in tasks:
        last_run = task.get("last_run") or {}
        if last_run.get("status") != "succeeded" or last_run.get("job_status") != "succeeded":
            continue
        detail = api.call("GET", f"/analysis/inference-runs/{last_run['id']}")
        if (detail.get("metrics") or {}).get("engine") != "semantica.reasoning.DatalogReasoner":
            continue
        successful_analysis += 1
        items = detail.get("items") or []
        analysis_results += len(items)
        evidence_linked_results += sum(
            bool(item.get("evidence"))
            and all(evidence.get("source_chunk_id") for evidence in item.get("evidence") or [])
            for item in items
        )
        expected = expected_analysis_results.get(str(task.get("name") or ""))
        if expected and any(
            expected[0].casefold() in str(item.get("subject_name") or "").casefold()
            and str(item.get("predicate") or "") == expected[1]
            and expected[2].casefold() in str(item.get("object_name") or "").casefold()
            for item in items
        ):
            expected_analysis_hits += 1
    checks.append(
        _check(
            "Semantica 分析任务",
            successful_analysis >= 3
            and analysis_results >= 3
            and evidence_linked_results >= 3
            and expected_analysis_hits == 3,
            f"{successful_analysis}/{len(tasks)} 个真实成功运行 / {analysis_results} 条结论 / "
            f"{evidence_linked_results} 条具备完整片段证据 / "
            f"{expected_analysis_hits}/3 条预期结论命中",
        )
    )

    search_results: dict[str, Any] = {}
    for mode, case in SEARCH_MODE_CASES.items():
        flags = case["flags"]
        payload = api.call(
            "POST", "/search",
            json={
                "query": "东方智造交付延期会影响哪些项目和责任部门",
                "space_ids": [space_id],
                "top_k": 10,
                "use_keyword": flags[0],
                "use_vector": flags[1],
                "use_graph": flags[2],
                "use_reranker": case["use_reranker"],
                "filters": {},
            },
        )
        count = len(payload.get("items") or [])
        counts = payload.get("channel_counts") or {}
        channel_ok = _search_result_contract(
            payload,
            case["channels"],
            reranker_requested=case["use_reranker"],
        )
        search_results[mode] = {
            "items": count,
            "channel_counts": counts,
            "warnings": payload.get("warnings") or [],
            "reranker_requested": case["use_reranker"],
            "reranker_applied": (payload.get("trace_summary") or {}).get("reranker_applied") is True,
        }
        rerank_detail = ""
        if case["use_reranker"]:
            rerank_detail = (
                "；重排已执行" if search_results[mode]["reranker_applied"]
                else "；重排不可用并已明确降级"
            )
        checks.append(
            _check(
                case["label"],
                channel_ok,
                f"召回 {count} 条；通道 {counts}{rerank_detail}",
            )
        )

    jobs = api.call("GET", f"/jobs?space_id={space_id}")
    failed_jobs, historical_failed_jobs = _latest_failed_jobs(
        jobs,
        current_version_ids={
            str(row.get("current_version_id"))
            for row in documents
            if row.get("current_version_id")
        },
    )
    checks.append(
        _check(
            "未解决失败任务",
            not failed_jobs,
            f"{len(failed_jobs)} 个未解决；{historical_failed_jobs} 个历史失败记录保留用于审计",
        )
    )

    ready = all(row["ok"] for row in checks if row["required"])
    result = {
        "ready": ready,
        "demo_data_notice": DEMO_NOTICE,
        "space_id": space_id,
        "models": {
            "live_tests_executed": live_model_tests,
            "test_results": model_test_results,
            "route_audit": route_audit,
            "kimi_total": len(kimi_models),
            "kimi_usable": len(kimi_usable),
        },
        "ground_truth": {
            "fact_count": len(fact_ground_truth.get("facts") or []),
            "document_question_count": len(fact_ground_truth.get("questions") or []),
            "structured_question_count": len(structured_questions),
            "graph_evidence": graph_evidence_ground_truth,
            "database": database_ground_truth,
            "structured_api": structured_api_ground_truth,
        },
        "counts": {
            "documents": len(documents),
            "published_documents": published,
            "profiles": len(profiles),
            "audio_segments": len(audio_segments),
            "video_frames": len(video_frames),
            "video_timeline_items": len(video_timeline),
            "sources": len(sources),
            "account_free_sources": len(account_free_sources),
            "account_free_sources_connected": account_free_connections,
            "account_free_sources_unchanged": account_free_unchanged,
            "database_sources": len(database_sources),
            "database_schema_tables": {
                str((source.get("config") or {}).get("dialect") or source.get("id")): sum(
                    row.get("kind") == "table"
                    for row in database_objects_by_source.get(str(source.get("id")), [])
                )
                for source in database_sources
            },
            "sensitive_preview_checks": sensitive_preview_checks,
            "blocked_filter_checks": blocked_filter_checks,
            "empty_preview_checks": empty_preview_checks,
            "no_primary_key_checks": no_primary_key_checks,
            "active_mappings": len(valid_active_mappings),
            "ontology_terms": len(terms),
            "entities": len(entities),
            "facts": len(facts),
            "analysis_tasks": len(tasks),
        },
        "failed_documents": failed_documents,
        "unresolved_failed_jobs": len(failed_jobs),
        "historical_failed_jobs": historical_failed_jobs,
        "releases": release_counts,
        "search": search_results,
        "checks": checks,
    }
    if _report_has_secret(result):
        # Fail closed and never render the offending report. This guards future
        # additions from echoing connector/model credentials in preflight logs.
        raise DemoError("演示预检报告包含疑似敏感字段，已阻止输出")
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="通过真实、非破坏性调用验证国联集团演示环境")
    value.add_argument("--compact", action="store_true", help="输出单行 JSON")
    value.add_argument("--no-strict", action="store_true", help="未就绪时仍返回退出码 0")
    value.add_argument(
        "--skip-live-model-tests",
        action="store_true",
        help="只读取最近模型测试状态；正式演示预检不应使用该选项",
    )
    value.add_argument(
        "--skip-database-ground-truth-tests",
        action="store_true",
        help="跳过 MySQL/PostgreSQL 各 20 条确定性 Ground Truth；正式预检不应使用",
    )
    value.add_argument(
        "--skip-structured-api-tests",
        action="store_true",
        help="跳过 20 条自然语言结构化查询 API 验证；正式预检不应使用",
    )
    value.add_argument(
        "--skip-service-chain-tests",
        action="store_true",
        help="跳过 REST/DSH/MCP/CLI 真实服务链；正式预检不应使用",
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        username, password = admin_credentials_from_environment()
        with SafeApiClient(
            base_url=api_url_from_environment(), username=username, password=password, timeout_seconds=600,
        ) as api:
            result = verify(
                api,
                live_model_tests=not args.skip_live_model_tests,
                database_ground_truth_tests=not args.skip_database_ground_truth_tests,
                structured_api_tests=not args.skip_structured_api_tests,
            )
            governance = GovernanceDemoPreparer(
                api,
                exercise=False,
                wait_timeout_seconds=600,
            ).run().as_dict()
            supported_governance = [
                row for row in governance.get("scenarios", [])
                if row.get("support") == "supported"
            ]
            failed_governance = [
                row for row in supported_governance
                if row.get("status") == "failed"
            ]
            result["governance"] = governance
            result["checks"].append(_check(
                "人工治理演示闭环",
                bool(supported_governance) and not failed_governance,
                f"{len(supported_governance) - len(failed_governance)}/"
                f"{len(supported_governance)} 个已支持场景就绪；"
                f"失败 {len(failed_governance)} 个",
            ))
        if args.skip_service_chain_tests:
            service_chain = {
                "ready": False,
                "checks": [_check(
                    "REST / DSH / MCP / CLI 服务链",
                    False,
                    "本次显式跳过；不能据此宣称演示服务已经打通",
                )],
                "metrics": {},
            }
        else:
            from scripts.demo.verify_guolian_service_chain import (
                _default_mcp_url,
                verify_service_chain,
            )

            service_api_url = api_url_from_environment()
            service_chain = verify_service_chain(
                api_url=service_api_url,
                mcp_url=_default_mcp_url(service_api_url),
                username=username,
                password=password,
            )
        result["service_chain"] = service_chain
        result["checks"].extend(service_chain.get("checks") or [])
        result["ready"] = all(
            row["ok"] for row in result["checks"] if row.get("required", True)
        )
        if _report_has_secret(result):
            raise DemoError("合并服务链后的预检报告包含疑似敏感字段，已阻止输出")
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        return 0 if result["ready"] or args.no_strict else 2
    except Exception as exc:
        print(redact({"ready": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
