#!/usr/bin/env python3
"""Validate the synthetic Guolian demo ground truth and optional database checks.

The checked-in JSON files are the source of truth for demo assertions.  This
module deliberately does not know platform credentials.  Database URLs are
accepted explicitly or read from a caller-selected environment variable, and
neither URLs nor bound parameters are included in errors or reports.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError


DEMO_DATA_NOTICE = "演示数据，不代表国联集团真实经营数据。"
FACT_SCHEMA_VERSION = "chuanshen.guolian-demo-ground-truth/v1"
STRUCTURED_SCHEMA_VERSION = "chuanshen.guolian-structured-ground-truth/v1"
DEFAULT_DATABASE_URL_ENV = "GUOLIAN_DEMO_DATABASE_URL"

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_NAMED_BIND = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
_FORBIDDEN_SQL = re.compile(
    r"\b(?:insert|update|delete|drop|alter|create|truncate|merge|replace|upsert|"
    r"grant|revoke|copy|call|execute|vacuum|analyze|attach|detach|pragma|set|reset|into)\b",
    re.IGNORECASE,
)
_SYSTEM_CATALOG = re.compile(
    r"\b(?:information_schema|pg_catalog|pg_toast|mysql\.|performance_schema|sys\.)",
    re.IGNORECASE,
)


class GroundTruthValidationError(ValueError):
    """Raised when a ground-truth document violates the declared v1 schema."""

    def __init__(self, issues: Sequence[str]):
        self.issues = tuple(issues)
        super().__init__("Ground Truth 校验失败：\n- " + "\n- ".join(self.issues))


class DatabaseVerificationError(RuntimeError):
    """Raised for a safe database-check failure without exposing credentials."""


@dataclass(frozen=True)
class DatabaseCheckResult:
    """One deterministic database assertion result."""

    question_id: str
    passed: bool
    row_count: int
    mismatch: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "passed": self.passed,
            "row_count": self.row_count,
            "mismatch": self.mismatch,
        }


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _require_object(value: Any, path: str, issues: list[str]) -> dict[str, Any]:
    if not _is_object(value):
        issues.append(f"{path} 必须是对象")
        return {}
    return value


def _check_keys(
    value: Mapping[str, Any],
    *,
    path: str,
    required: set[str],
    optional: set[str],
    issues: list[str],
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        issues.append(f"{path} 缺少字段：{', '.join(missing)}")
    if unknown:
        issues.append(f"{path} 包含未知字段：{', '.join(unknown)}")


def _nonempty_string(value: Any, path: str, issues: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{path} 必须是非空字符串")
        return ""
    return value.strip()


def _identifier(value: Any, path: str, issues: list[str]) -> str:
    result = _nonempty_string(value, path, issues)
    if result and not _IDENTIFIER.fullmatch(result):
        issues.append(f"{path} 不是稳定标识符")
    return result


def _string_list(value: Any, path: str, issues: list[str], *, allow_empty: bool = True) -> list[str]:
    if not _is_list(value):
        issues.append(f"{path} 必须是字符串数组")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        text_value = _nonempty_string(item, f"{path}[{index}]", issues)
        if text_value:
            result.append(text_value)
    if not allow_empty and not result:
        issues.append(f"{path} 不能为空")
    return result


def _nonnegative_number(value: Any, path: str, issues: list[str]) -> Decimal | None:
    if isinstance(value, bool):
        issues.append(f"{path} 必须是非负数")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        issues.append(f"{path} 必须是非负数")
        return None
    if not result.is_finite() or result < 0:
        issues.append(f"{path} 必须是有限非负数")
        return None
    return result


def _validate_header(
    payload: dict[str, Any],
    expected_version: str,
    path: str,
    issues: list[str],
) -> None:
    if payload.get("schema_version") != expected_version:
        issues.append(f"{path}.schema_version 必须为 {expected_version}")
    _identifier(payload.get("dataset"), f"{path}.dataset", issues)
    if payload.get("demo_data_notice") != DEMO_DATA_NOTICE:
        issues.append(f"{path}.demo_data_notice 必须精确声明：{DEMO_DATA_NOTICE}")
    if payload.get("synthetic") is not True:
        issues.append(f"{path}.synthetic 必须为 true")


def _validate_source(source: Any, path: str, issues: list[str]) -> None:
    item = _require_object(source, path, issues)
    _check_keys(
        item,
        path=path,
        required={"filename"},
        optional={
            "document_version",
            "page_number",
            "slide_number",
            "worksheet",
            "row_number",
            "time_start",
            "time_end",
            "structural_path",
            "chunk_structural_path",
        },
        issues=issues,
    )
    _nonempty_string(item.get("filename"), f"{path}.filename", issues)
    if "document_version" in item:
        _nonempty_string(item.get("document_version"), f"{path}.document_version", issues)

    for key in ("page_number", "slide_number", "row_number"):
        if key in item and (isinstance(item[key], bool) or not isinstance(item[key], int) or item[key] < 1):
            issues.append(f"{path}.{key} 必须是从 1 开始的整数")

    if "worksheet" in item:
        _nonempty_string(item.get("worksheet"), f"{path}.worksheet", issues)
        if "row_number" not in item:
            issues.append(f"{path}.worksheet 必须与 row_number 同时提供")
    if "row_number" in item and "worksheet" not in item:
        issues.append(f"{path}.row_number 必须与 worksheet 同时提供")

    has_start = "time_start" in item
    has_end = "time_end" in item
    if has_start != has_end:
        issues.append(f"{path}.time_start 与 time_end 必须同时提供")
    if has_start and has_end:
        start = _nonnegative_number(item.get("time_start"), f"{path}.time_start", issues)
        end = _nonnegative_number(item.get("time_end"), f"{path}.time_end", issues)
        if start is not None and end is not None and end < start:
            issues.append(f"{path}.time_end 不得早于 time_start")

    if "structural_path" in item:
        _nonempty_string(item.get("structural_path"), f"{path}.structural_path", issues)
    if "chunk_structural_path" in item:
        _nonempty_string(
            item.get("chunk_structural_path"),
            f"{path}.chunk_structural_path",
            issues,
        )

    has_locator = any(
        key in item
        for key in ("page_number", "slide_number", "worksheet", "time_start", "structural_path")
    )
    if not has_locator:
        issues.append(
            f"{path} 至少需要页码、幻灯片、工作表行、时间区间或结构路径之一"
        )


def _validate_relation(value: Any, path: str, issues: list[str]) -> None:
    relation = _require_object(value, path, issues)
    _check_keys(
        relation,
        path=path,
        required={"subject", "predicate", "object"},
        optional=set(),
        issues=issues,
    )
    for key in ("subject", "predicate", "object"):
        _nonempty_string(relation.get(key), f"{path}.{key}", issues)


def validate_demo_ground_truth(payload: Any) -> dict[str, Any]:
    """Validate document, graph, governance, and retrieval expectations."""

    issues: list[str] = []
    root = _require_object(payload, "demo_ground_truth", issues)
    _check_keys(
        root,
        path="demo_ground_truth",
        required={"schema_version", "dataset", "demo_data_notice", "synthetic", "facts", "questions"},
        optional=set(),
        issues=issues,
    )
    _validate_header(root, FACT_SCHEMA_VERSION, "demo_ground_truth", issues)

    facts = root.get("facts")
    if not _is_list(facts) or not facts:
        issues.append("demo_ground_truth.facts 必须是非空数组")
        facts = []
    fact_ids: set[str] = set()
    for index, raw_fact in enumerate(facts):
        path = f"demo_ground_truth.facts[{index}]"
        fact = _require_object(raw_fact, path, issues)
        _check_keys(
            fact,
            path=path,
            required={
                "fact_id",
                "statement",
                "source",
                "expected_entities",
                "expected_relations",
                "current",
                "historical",
                "governance_issue",
            },
            optional={
                "expected_classification",
                "expected_tags",
                "seeded_graph",
                "required_for_inference",
                "evidence_terms",
            },
            issues=issues,
        )
        fact_id = _identifier(fact.get("fact_id"), f"{path}.fact_id", issues)
        if fact_id in fact_ids:
            issues.append(f"{path}.fact_id 重复：{fact_id}")
        fact_ids.add(fact_id)
        _nonempty_string(fact.get("statement"), f"{path}.statement", issues)
        _validate_source(fact.get("source"), f"{path}.source", issues)
        _string_list(fact.get("expected_entities"), f"{path}.expected_entities", issues)
        relations = fact.get("expected_relations")
        if not _is_list(relations):
            issues.append(f"{path}.expected_relations 必须是数组")
        else:
            for relation_index, relation in enumerate(relations):
                _validate_relation(relation, f"{path}.expected_relations[{relation_index}]", issues)
        for key in ("current", "historical", "governance_issue"):
            if not isinstance(fact.get(key), bool):
                issues.append(f"{path}.{key} 必须是布尔值")
        if fact.get("current") is True and fact.get("historical") is True:
            issues.append(f"{path} 不能同时标记为 current 和 historical")
        if "expected_classification" in fact:
            _nonempty_string(fact.get("expected_classification"), f"{path}.expected_classification", issues)
        if "expected_tags" in fact:
            _string_list(fact.get("expected_tags"), f"{path}.expected_tags", issues)
        seeded_graph = fact.get("seeded_graph", False)
        required_for_inference = fact.get("required_for_inference", False)
        for key, value in (
            ("seeded_graph", seeded_graph),
            ("required_for_inference", required_for_inference),
        ):
            if key in fact and not isinstance(value, bool):
                issues.append(f"{path}.{key} 必须是布尔值")
        evidence_terms = fact.get("evidence_terms", [])
        if "evidence_terms" in fact:
            evidence_terms = _string_list(
                evidence_terms,
                f"{path}.evidence_terms",
                issues,
                allow_empty=False,
            )
        if seeded_graph is True:
            if fact.get("current") is not True or fact.get("historical") is not False:
                issues.append(f"{path} 的 seeded_graph 事实必须是当前有效知识")
            if not _is_list(relations) or len(relations) != 1:
                issues.append(f"{path} 的 seeded_graph 事实必须且只能声明一条关系")
            if not evidence_terms:
                issues.append(f"{path} 的 seeded_graph 事实必须声明 evidence_terms")
            source = fact.get("source") if _is_object(fact.get("source")) else {}
            if not source.get("chunk_structural_path"):
                issues.append(
                    f"{path} 的 seeded_graph 事实必须声明 source.chunk_structural_path"
                )
            if _is_list(relations) and len(relations) == 1 and evidence_terms:
                relation = relations[0] if _is_object(relations[0]) else {}
                normalized_terms = "".join(str(value).casefold() for value in evidence_terms)
                for key in ("subject", "predicate", "object"):
                    component = str(relation.get(key) or "").casefold()
                    if component and component not in normalized_terms:
                        issues.append(
                            f"{path}.evidence_terms 必须覆盖关系字段 {key}={relation.get(key)}"
                        )
        if required_for_inference is True and seeded_graph is not True:
            issues.append(f"{path}.required_for_inference 只能用于 seeded_graph 事实")

    questions = root.get("questions")
    if not _is_list(questions) or not questions:
        issues.append("demo_ground_truth.questions 必须是非空数组")
        questions = []
    question_ids: set[str] = set()
    for index, raw_question in enumerate(questions):
        path = f"demo_ground_truth.questions[{index}]"
        question = _require_object(raw_question, path, issues)
        _check_keys(
            question,
            path=path,
            required={"question_id", "question", "expected_answer_points", "fact_ids"},
            optional=set(),
            issues=issues,
        )
        question_id = _identifier(question.get("question_id"), f"{path}.question_id", issues)
        if question_id in question_ids:
            issues.append(f"{path}.question_id 重复：{question_id}")
        question_ids.add(question_id)
        _nonempty_string(question.get("question"), f"{path}.question", issues)
        _string_list(
            question.get("expected_answer_points"),
            f"{path}.expected_answer_points",
            issues,
            allow_empty=False,
        )
        linked = _string_list(question.get("fact_ids"), f"{path}.fact_ids", issues, allow_empty=False)
        unknown = sorted(set(linked) - fact_ids)
        if unknown:
            issues.append(f"{path}.fact_ids 引用了不存在的事实：{', '.join(unknown)}")

    if issues:
        raise GroundTruthValidationError(issues)
    return root


def _validate_tolerance(value: Any, expected: Any, path: str, issues: list[str]) -> None:
    if _is_object(value):
        for key, item in value.items():
            _nonempty_string(key, f"{path} 的列名", issues)
            _nonnegative_number(item, f"{path}.{key}", issues)
        if not value:
            issues.append(f"{path} 不能为空对象")
        if not isinstance(expected, (list, dict)):
            issues.append(f"{path} 仅可在 expected_value 为行数组或对象时使用按列容差")
        return
    _nonnegative_number(value, path, issues)


def validate_readonly_sql(sql: Any, parameters: Any, path: str = "database_check") -> tuple[str, Any]:
    """Validate one SELECT and normalize positional binds to SQLAlchemy names."""

    issues: list[str] = []
    query = _nonempty_string(sql, f"{path}.sql", issues)
    stripped = query.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        issues.append(f"{path}.sql 只能包含一条语句")
    if "--" in stripped or "/*" in stripped or "*/" in stripped:
        issues.append(f"{path}.sql 不允许 SQL 注释")
    if not re.match(r"^(?:select|with)\b", stripped, re.IGNORECASE):
        issues.append(f"{path}.sql 只允许 SELECT 或以 SELECT 结束的 WITH 查询")
    if _FORBIDDEN_SQL.search(stripped):
        issues.append(f"{path}.sql 包含写入或管理关键字")
    if _SYSTEM_CATALOG.search(stripped):
        issues.append(f"{path}.sql 不允许访问系统目录")

    named = set(_NAMED_BIND.findall(stripped))
    positional_count = stripped.count("?")
    normalized_parameters: Mapping[str, Any]
    if isinstance(parameters, dict):
        if positional_count:
            issues.append(f"{path}.parameters 为对象时 SQL 必须使用 :name 命名参数")
        keys = set(parameters)
        if named != keys:
            issues.append(
                f"{path}.parameters 必须与 SQL 命名参数完全一致"
            )
        normalized_parameters = dict(parameters)
    elif isinstance(parameters, list):
        if named:
            issues.append(f"{path}.parameters 为数组时 SQL 必须使用 ? 位置参数")
        if positional_count != len(parameters):
            issues.append(f"{path}.parameters 数量与 SQL 位置参数不一致")
        replacements = iter(f":p{index}" for index in range(positional_count))
        normalized_sql_parts = stripped.split("?")
        rebuilt = normalized_sql_parts[0]
        for suffix in normalized_sql_parts[1:]:
            rebuilt += next(replacements) + suffix
        stripped = rebuilt
        normalized_parameters = {f"p{index}": item for index, item in enumerate(parameters)}
    else:
        issues.append(f"{path}.parameters 必须是对象或数组")
        normalized_parameters = {}

    if issues:
        raise GroundTruthValidationError(issues)
    return stripped, normalized_parameters


def _validate_database_check(value: Any, expected: Any, path: str, issues: list[str]) -> None:
    check = _require_object(value, path, issues)
    _check_keys(
        check,
        path=path,
        required={"sql", "parameters", "result_mode"},
        optional={"value_column", "order_sensitive"},
        issues=issues,
    )
    mode = check.get("result_mode")
    if mode not in {"scalar", "object", "rows"}:
        issues.append(f"{path}.result_mode 必须是 scalar、object 或 rows")
    if mode == "object" and not isinstance(expected, dict):
        issues.append(f"{path}.result_mode 为 object 时 expected_value 必须是对象")
    if mode == "rows" and not isinstance(expected, list):
        issues.append(f"{path}.result_mode 为 rows 时 expected_value 必须是数组")
    if mode == "scalar" and "value_column" in check:
        _nonempty_string(check.get("value_column"), f"{path}.value_column", issues)
    if "order_sensitive" in check and not isinstance(check.get("order_sensitive"), bool):
        issues.append(f"{path}.order_sensitive 必须是布尔值")
    try:
        validate_readonly_sql(check.get("sql"), check.get("parameters"), path)
    except GroundTruthValidationError as exc:
        issues.extend(exc.issues)


def _validate_citation(value: Any, path: str, issues: list[str]) -> None:
    citation = _require_object(value, path, issues)
    _check_keys(
        citation,
        path=path,
        required={"source", "objects", "description"},
        optional=set(),
        issues=issues,
    )
    _nonempty_string(citation.get("source"), f"{path}.source", issues)
    _string_list(citation.get("objects"), f"{path}.objects", issues, allow_empty=False)
    _nonempty_string(citation.get("description"), f"{path}.description", issues)


def validate_structured_ground_truth(payload: Any) -> dict[str, Any]:
    """Validate deterministic structured-query expectations and safe checks."""

    issues: list[str] = []
    root = _require_object(payload, "structured_ground_truth", issues)
    _check_keys(
        root,
        path="structured_ground_truth",
        required={"schema_version", "dataset", "demo_data_notice", "synthetic", "questions"},
        optional=set(),
        issues=issues,
    )
    _validate_header(root, STRUCTURED_SCHEMA_VERSION, "structured_ground_truth", issues)
    questions = root.get("questions")
    if not _is_list(questions) or not questions:
        issues.append("structured_ground_truth.questions 必须是非空数组")
        questions = []
    question_ids: set[str] = set()
    for index, raw_question in enumerate(questions):
        path = f"structured_ground_truth.questions[{index}]"
        question = _require_object(raw_question, path, issues)
        _check_keys(
            question,
            path=path,
            required={
                "question_id",
                "question",
                "metric_definition",
                "time_range",
                "exclusions",
                "business_objects",
                "expected_value",
                "tolerance",
                "requires",
                "expected_citations",
            },
            optional={"database_check"},
            issues=issues,
        )
        question_id = _identifier(question.get("question_id"), f"{path}.question_id", issues)
        if question_id in question_ids:
            issues.append(f"{path}.question_id 重复：{question_id}")
        question_ids.add(question_id)
        _nonempty_string(question.get("question"), f"{path}.question", issues)
        _nonempty_string(question.get("metric_definition"), f"{path}.metric_definition", issues)
        _nonempty_string(question.get("time_range"), f"{path}.time_range", issues)
        _string_list(question.get("exclusions"), f"{path}.exclusions", issues)
        _string_list(question.get("business_objects"), f"{path}.business_objects", issues, allow_empty=False)
        if question.get("expected_value") is None:
            issues.append(f"{path}.expected_value 不能为 null")
        _validate_tolerance(
            question.get("tolerance"),
            question.get("expected_value"),
            f"{path}.tolerance",
            issues,
        )

        requirements = _require_object(question.get("requires"), f"{path}.requires", issues)
        _check_keys(
            requirements,
            path=f"{path}.requires",
            required={"aggregation", "join", "deduplication"},
            optional=set(),
            issues=issues,
        )
        for key in ("aggregation", "join", "deduplication"):
            if not isinstance(requirements.get(key), bool):
                issues.append(f"{path}.requires.{key} 必须是布尔值")

        citations = question.get("expected_citations")
        if not _is_list(citations) or not citations:
            issues.append(f"{path}.expected_citations 必须是非空数组")
        else:
            for citation_index, citation in enumerate(citations):
                _validate_citation(citation, f"{path}.expected_citations[{citation_index}]", issues)
        if "database_check" in question:
            _validate_database_check(
                question.get("database_check"),
                question.get("expected_value"),
                f"{path}.database_check",
                issues,
            )

    if issues:
        raise GroundTruthValidationError(issues)
    return root


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GroundTruthValidationError([f"文件不存在：{path}"]) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GroundTruthValidationError([f"文件不是有效 UTF-8 JSON：{path.name}"]) from exc


def load_and_validate_bundle(
    facts_path: Path,
    structured_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load both files and enforce identifiers across the complete demo bundle."""

    facts = validate_demo_ground_truth(load_json(facts_path))
    structured = validate_structured_ground_truth(load_json(structured_path))
    if facts["dataset"] != structured["dataset"]:
        raise GroundTruthValidationError([
            "两个 Ground Truth 文件的 dataset 必须一致"
        ])
    document_question_ids = {item["question_id"] for item in facts["questions"]}
    structured_question_ids = {item["question_id"] for item in structured["questions"]}
    overlap = sorted(document_question_ids & structured_question_ids)
    if overlap:
        raise GroundTruthValidationError([
            "两个 Ground Truth 文件的 question_id 必须全局唯一：" + ", ".join(overlap)
        ])
    return facts, structured


def database_url_from_environment(env_var: str = DEFAULT_DATABASE_URL_ENV) -> str:
    """Return a database URL without including its value in any failure text."""

    value = os.getenv(env_var, "").strip()
    if not value:
        raise DatabaseVerificationError(f"未配置数据库连接环境变量 {env_var}")
    return value


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _tolerance_for(tolerance: Any, field: str | None = None) -> Decimal:
    raw = tolerance.get(field, 0) if isinstance(tolerance, dict) and field is not None else tolerance
    number = _decimal(raw)
    return number if number is not None else Decimal(0)


def _values_match(expected: Any, actual: Any, tolerance: Any, field: str | None = None) -> bool:
    expected_number = _decimal(expected)
    actual_number = _decimal(actual)
    if expected_number is not None and actual_number is not None:
        return abs(expected_number - actual_number) <= _tolerance_for(tolerance, field)
    if isinstance(actual, (date, datetime, time)) and isinstance(expected, str):
        return expected == actual.isoformat()
    return expected == actual


def _object_mismatch(expected: dict[str, Any], actual: dict[str, Any], tolerance: Any) -> str | None:
    if set(expected) != set(actual):
        return "单行对象列集合不一致"
    for field, expected_value in expected.items():
        if not _values_match(expected_value, actual[field], tolerance, field):
            return f"单行对象字段 {field} 与预期不一致"
    return None


def _row_mismatch(expected_rows: list[Any], actual_rows: list[dict[str, Any]], tolerance: Any) -> str | None:
    if len(expected_rows) != len(actual_rows):
        return f"预期 {len(expected_rows)} 行，实际 {len(actual_rows)} 行"
    for row_index, (expected, actual) in enumerate(zip(expected_rows, actual_rows, strict=True)):
        if not isinstance(expected, dict):
            return f"预期结果第 {row_index + 1} 行不是对象"
        if set(expected) != set(actual):
            return f"第 {row_index + 1} 行列集合不一致"
        for field, expected_value in expected.items():
            if not _values_match(expected_value, actual[field], tolerance, field):
                return f"第 {row_index + 1} 行字段 {field} 与预期不一致"
    return None


def execute_database_check(
    engine: Engine,
    question: Mapping[str, Any],
    *,
    max_rows: int = 1000,
) -> DatabaseCheckResult:
    """Execute one validated, parameter-bound check through a caller-owned engine."""

    check = question.get("database_check")
    if not isinstance(check, dict):
        raise DatabaseVerificationError("问题没有配置 database_check")
    sql, parameters = validate_readonly_sql(check.get("sql"), check.get("parameters"))
    question_id = str(question.get("question_id") or "unknown")
    try:
        with engine.connect() as connection:
            dialect = connection.dialect.name
            transaction = connection.begin()
            try:
                if dialect == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                elif dialect in {"mysql", "mariadb"}:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                result = connection.execute(text(sql), parameters)
                rows = [dict(row) for row in result.mappings().fetchmany(max_rows + 1)]
            finally:
                transaction.rollback()
    except (SQLAlchemyError, OSError) as exc:
        raise DatabaseVerificationError(
            f"数据库核验 {question_id} 执行失败（{type(exc).__name__}）"
        ) from exc
    if len(rows) > max_rows:
        raise DatabaseVerificationError(f"数据库核验 {question_id} 超过最大返回行数 {max_rows}")

    expected = question.get("expected_value")
    tolerance = question.get("tolerance", 0)
    mode = check.get("result_mode")
    mismatch: str | None
    if mode == "scalar":
        if len(rows) != 1 or not rows[0]:
            mismatch = f"标量查询预期 1 行，实际 {len(rows)} 行"
        else:
            column = check.get("value_column") or next(iter(rows[0]))
            if column not in rows[0]:
                mismatch = "标量查询缺少指定结果列"
            elif not _values_match(expected, rows[0][column], tolerance, str(column)):
                mismatch = "标量结果超出允许容差"
            else:
                mismatch = None
    elif mode == "object":
        if not isinstance(expected, dict):
            mismatch = "单行对象的 expected_value 必须是对象"
        elif len(rows) != 1:
            mismatch = f"单行对象查询预期 1 行，实际 {len(rows)} 行"
        else:
            mismatch = _object_mismatch(expected, rows[0], tolerance)
    elif mode == "rows":
        if not isinstance(expected, list):
            mismatch = "行结果的 expected_value 必须是数组"
        else:
            actual_rows = rows
            expected_rows = expected
            if check.get("order_sensitive", True) is False:
                key = lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                actual_rows = sorted(actual_rows, key=key)
                expected_rows = sorted(expected_rows, key=key)
            mismatch = _row_mismatch(expected_rows, actual_rows, tolerance)
    else:
        mismatch = "未知 result_mode"
    return DatabaseCheckResult(question_id, mismatch is None, len(rows), mismatch)


def verify_database_ground_truth(
    payload: Any,
    *,
    engine: Engine | None = None,
    database_url: str | None = None,
    database_url_env: str = DEFAULT_DATABASE_URL_ENV,
) -> list[DatabaseCheckResult]:
    """Execute every declared database check without logging URL or parameters."""

    structured = validate_structured_ground_truth(payload)
    questions = [
        question
        for question in structured["questions"]
        if "database_check" in question
    ]
    if not questions:
        return []
    owned_engine = engine is None
    selected_engine = engine
    if selected_engine is None:
        url = database_url or database_url_from_environment(database_url_env)
        try:
            selected_engine = create_engine(url, pool_pre_ping=True)
        except (SQLAlchemyError, ValueError, OSError) as exc:
            raise DatabaseVerificationError(
                f"无法创建数据库核验连接（{type(exc).__name__}）"
            ) from exc
    try:
        return [
            execute_database_check(selected_engine, question)
            for question in questions
        ]
    finally:
        if owned_engine and selected_engine is not None:
            selected_engine.dispose()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验国联集团演示 Ground Truth")
    parser.add_argument("--root", type=Path, default=Path("demo/guolian"))
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--structured", type=Path)
    parser.add_argument("--database-checks", action="store_true")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    facts_path = args.facts or args.root / "demo_ground_truth.json"
    structured_path = args.structured or args.root / "structured_query_ground_truth.json"
    try:
        facts, structured = load_and_validate_bundle(facts_path, structured_path)
        database_results = (
            verify_database_ground_truth(structured, database_url_env=args.database_url_env)
            if args.database_checks
            else []
        )
        failed = [item for item in database_results if not item.passed]
        report = {
            "status": "failed" if failed else "passed",
            "dataset": facts["dataset"],
            "facts": len(facts["facts"]),
            "document_questions": len(facts["questions"]),
            "structured_questions": len(structured["questions"]),
            "database_checks": len(database_results),
            "database_checks_passed": len(database_results) - len(failed),
            "database_results": [item.as_dict() for item in database_results],
        }
        if args.json_output:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(
                "Ground Truth 校验通过："
                f"{report['facts']} 条事实，"
                f"{report['document_questions']} 个知识问题，"
                f"{report['structured_questions']} 个结构化问题"
            )
            if database_results:
                print(
                    f"数据库直接核验：{report['database_checks_passed']}/"
                    f"{report['database_checks']} 通过"
                )
                for item in failed:
                    print(f"- {item.question_id}: {item.mismatch}")
        return 1 if failed else 0
    except (GroundTruthValidationError, DatabaseVerificationError) as exc:
        if args.json_output:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        else:
            print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
