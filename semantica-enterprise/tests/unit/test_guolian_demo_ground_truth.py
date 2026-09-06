from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from scripts.demo.verify_guolian_ground_truth import (
    DEMO_DATA_NOTICE,
    FACT_SCHEMA_VERSION,
    STRUCTURED_SCHEMA_VERSION,
    DatabaseVerificationError,
    GroundTruthValidationError,
    database_url_from_environment,
    execute_database_check,
    load_and_validate_bundle,
    validate_demo_ground_truth,
    validate_readonly_sql,
    validate_structured_ground_truth,
    verify_database_ground_truth,
)


def demo_payload() -> dict:
    return {
        "schema_version": FACT_SCHEMA_VERSION,
        "dataset": "guolian-enterprise-demo-v1",
        "demo_data_notice": DEMO_DATA_NOTICE,
        "synthetic": True,
        "facts": [
            {
                "fact_id": "fact-policy-scope",
                "statement": "采购实施细则适用于国联集团。",
                "source": {"filename": "采购实施细则.md", "structural_path": "第二章/适用范围"},
                "expected_entities": ["采购实施细则", "国联集团"],
                "expected_relations": [
                    {"subject": "采购实施细则", "predicate": "适用于", "object": "国联集团"}
                ],
                "expected_classification": "管理制度/实施细则",
                "expected_tags": ["采购"],
                "current": True,
                "historical": False,
                "governance_issue": False,
            },
            {
                "fact_id": "fact-meeting-risk",
                "statement": "项目例会提到供应商交付延期。",
                "source": {
                    "filename": "项目例会.wav",
                    "time_start": 12.5,
                    "time_end": 18.2,
                },
                "expected_entities": ["东方智造"],
                "expected_relations": [],
                "current": True,
                "historical": False,
                "governance_issue": False,
            },
        ],
        "questions": [
            {
                "question_id": "doc-policy-scope",
                "question": "采购实施细则适用于谁？",
                "expected_answer_points": ["国联集团"],
                "fact_ids": ["fact-policy-scope"],
            }
        ],
    }


def structured_payload() -> dict:
    return {
        "schema_version": STRUCTURED_SCHEMA_VERSION,
        "dataset": "guolian-enterprise-demo-v1",
        "demo_data_notice": DEMO_DATA_NOTICE,
        "synthetic": True,
        "questions": [
            {
                "question_id": "sql-procurement-total",
                "question": "2026 年有效采购总额是多少？",
                "metric_definition": "汇总已完成且未取消采购订单金额。",
                "time_range": "2026-01-01 至 2026-12-31",
                "exclusions": ["取消订单"],
                "business_objects": ["采购订单"],
                "expected_value": 300.0,
                "tolerance": 0.01,
                "requires": {"aggregation": True, "join": False, "deduplication": False},
                "expected_citations": [
                    {
                        "source": "集团经营演示库",
                        "objects": ["purchase_orders"],
                        "description": "2026 年已完成订单",
                    }
                ],
                "database_check": {
                    "sql": (
                        "SELECT SUM(amount) AS total FROM purchase_orders "
                        "WHERE status = :status AND order_date >= :start_date"
                    ),
                    "parameters": {"status": "completed", "start_date": "2026-01-01"},
                    "result_mode": "scalar",
                    "value_column": "total",
                },
            }
        ],
    }


def test_valid_ground_truth_enforces_notice_relations_and_media_locator() -> None:
    result = validate_demo_ground_truth(demo_payload())
    assert len(result["facts"]) == 2
    assert result["facts"][1]["source"]["time_start"] == 12.5


def test_seeded_graph_fact_requires_current_exact_relation_chunk_and_evidence_terms() -> None:
    payload = demo_payload()
    seeded = payload["facts"][0]
    seeded["source"]["chunk_structural_path"] = "document"
    seeded["seeded_graph"] = True
    seeded["required_for_inference"] = True
    seeded["evidence_terms"] = ["采购实施细则", "适用于", "国联集团"]
    assert validate_demo_ground_truth(payload)["facts"][0]["seeded_graph"] is True

    invalid = copy.deepcopy(payload)
    invalid["facts"][0]["evidence_terms"] = ["采购实施细则", "适用于"]
    invalid["facts"][0]["source"].pop("chunk_structural_path")
    with pytest.raises(GroundTruthValidationError) as raised:
        validate_demo_ground_truth(invalid)
    message = str(raised.value)
    assert "evidence_terms 必须覆盖关系字段 object=国联集团" in message
    assert "source.chunk_structural_path" in message


def test_checked_in_graph_seed_contract_has_nine_evidenced_facts_and_eight_premises() -> None:
    payload = validate_demo_ground_truth(
        json.loads(Path("demo/guolian/demo_ground_truth.json").read_text(encoding="utf-8"))
    )
    seeded = [row for row in payload["facts"] if row.get("seeded_graph") is True]
    premises = [row for row in seeded if row.get("required_for_inference") is True]
    assert len(seeded) == 9
    assert len(premises) == 8
    assert all((row.get("source") or {}).get("chunk_structural_path") for row in seeded)
    assert all(row.get("evidence_terms") for row in seeded)

    responsibility = next(row for row in seeded if row["fact_id"] == "FACT-DEMO-008")
    assert responsibility["expected_relations"] == [{
        "subject": "数字科技公司",
        "predicate": "负责",
        "object": "智慧流程中枢项目",
    }]
    premise_source = Path("demo/guolian/规则推演前提事实（演示版）.txt")
    assert premise_source.is_file()
    assert all(
        row["source"]["filename"] == premise_source.name
        for row in seeded
        if row["fact_id"] in {"FACT-DEMO-026", "FACT-DEMO-027", "FACT-DEMO-028", "FACT-DEMO-029"}
    )


def test_demo_ground_truth_rejects_duplicate_ids_unknown_fields_and_bad_locator() -> None:
    payload = demo_payload()
    duplicate = copy.deepcopy(payload["facts"][0])
    duplicate["source"] = {"filename": "无定位.md"}
    duplicate["unexpected"] = True
    payload["facts"].append(duplicate)
    payload["demo_data_notice"] = "真实数据"

    with pytest.raises(GroundTruthValidationError) as raised:
        validate_demo_ground_truth(payload)

    message = str(raised.value)
    assert "fact_id 重复" in message
    assert "包含未知字段" in message
    assert "至少需要页码" in message
    assert DEMO_DATA_NOTICE in message


def test_questions_must_reference_existing_facts_and_be_unique() -> None:
    payload = demo_payload()
    payload["questions"].append({
        "question_id": "doc-policy-scope",
        "question": "重复问题",
        "expected_answer_points": ["无"],
        "fact_ids": ["missing-fact"],
    })
    with pytest.raises(GroundTruthValidationError) as raised:
        validate_demo_ground_truth(payload)
    assert "question_id 重复" in str(raised.value)
    assert "不存在的事实" in str(raised.value)


def test_structured_schema_requires_nonnegative_numeric_tolerance_and_citation() -> None:
    payload = structured_payload()
    assert validate_structured_ground_truth(payload)["questions"][0]["tolerance"] == 0.01

    payload["questions"][0]["tolerance"] = -1
    payload["questions"][0]["expected_citations"] = []
    with pytest.raises(GroundTruthValidationError) as raised:
        validate_structured_ground_truth(payload)
    assert "有限非负数" in str(raised.value)
    assert "expected_citations 必须是非空数组" in str(raised.value)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE purchase_orders SET amount = 0",
        "SELECT 1; DROP TABLE purchase_orders",
        "SELECT * FROM information_schema.tables",
        "WITH removed AS (DELETE FROM purchase_orders RETURNING *) SELECT * FROM removed",
        "SELECT * FROM purchase_orders -- bypass",
    ],
)
def test_database_checks_reject_writes_multiple_statements_and_catalogs(sql: str) -> None:
    with pytest.raises(GroundTruthValidationError):
        validate_readonly_sql(sql, {})


def test_named_and_positional_parameters_are_bound_and_must_match() -> None:
    sql, parameters = validate_readonly_sql("SELECT :amount AS value", {"amount": 12})
    assert sql == "SELECT :amount AS value"
    assert parameters == {"amount": 12}

    positional_sql, positional = validate_readonly_sql("SELECT ? + ? AS value", [4, 8])
    assert positional_sql == "SELECT :p0 + :p1 AS value"
    assert positional == {"p0": 4, "p1": 8}

    with pytest.raises(GroundTruthValidationError, match="完全一致"):
        validate_readonly_sql("SELECT :amount AS value", {"other": 12})


def test_database_direct_calculation_is_read_only_parameterized_and_tolerance_aware() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE purchase_orders ("
                "id INTEGER PRIMARY KEY, order_date TEXT, status TEXT, amount NUMERIC)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO purchase_orders (id, order_date, status, amount) "
                "VALUES (:id, :date, :status, :amount)"
            ),
            [
                {"id": 1, "date": "2026-01-10", "status": "completed", "amount": 100},
                {"id": 2, "date": "2026-02-10", "status": "completed", "amount": 200},
                {"id": 3, "date": "2026-03-10", "status": "cancelled", "amount": 999},
            ],
        )
    try:
        result = execute_database_check(engine, structured_payload()["questions"][0])
        assert result.passed is True
        assert result.row_count == 1
        assert result.mismatch is None
    finally:
        engine.dispose()


def test_database_object_mode_matches_one_row_and_per_field_tolerance() -> None:
    question = copy.deepcopy(structured_payload()["questions"][0])
    question["expected_value"] = {"numerator": 1700000, "percent": 80.95238}
    question["tolerance"] = {"numerator": 0, "percent": 0.001}
    question["database_check"] = {
        "sql": "SELECT :numerator AS numerator, :percent AS percent",
        "parameters": {"numerator": 1700000, "percent": 80.952381},
        "result_mode": "object",
    }
    validate_structured_ground_truth({
        **structured_payload(),
        "questions": [question],
    })
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        result = execute_database_check(engine, question)
        assert result.passed is True
        assert result.row_count == 1
    finally:
        engine.dispose()


def test_database_object_mode_rejects_non_object_expectation_and_extra_columns() -> None:
    question = copy.deepcopy(structured_payload()["questions"][0])
    question["database_check"]["result_mode"] = "object"
    with pytest.raises(GroundTruthValidationError, match="expected_value 必须是对象"):
        validate_structured_ground_truth({**structured_payload(), "questions": [question]})

    question["expected_value"] = {"total": 300}
    question["database_check"] = {
        "sql": "SELECT :total AS total, :extra AS extra",
        "parameters": {"total": 300, "extra": 1},
        "result_mode": "object",
    }
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        result = execute_database_check(engine, question)
        assert result.passed is False
        assert result.mismatch == "单行对象列集合不一致"
    finally:
        engine.dispose()


def test_database_result_reports_safe_mismatch_without_sql_or_parameters() -> None:
    payload = structured_payload()["questions"][0]
    payload["expected_value"] = 301
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE purchase_orders (order_date TEXT, status TEXT, amount NUMERIC)")
        )
    try:
        result = execute_database_check(engine, payload)
        assert result.passed is False
        assert result.mismatch == "标量结果超出允许容差"
        assert "completed" not in result.mismatch
        assert "SELECT" not in result.mismatch
    finally:
        engine.dispose()


def test_bundle_rejects_question_ids_shared_by_both_files(tmp_path) -> None:
    facts = demo_payload()
    structured = structured_payload()
    structured["questions"][0]["question_id"] = facts["questions"][0]["question_id"]
    facts_path = tmp_path / "demo_ground_truth.json"
    structured_path = tmp_path / "structured_query_ground_truth.json"
    facts_path.write_text(__import__("json").dumps(facts, ensure_ascii=False), encoding="utf-8")
    structured_path.write_text(__import__("json").dumps(structured, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(GroundTruthValidationError, match="全局唯一"):
        load_and_validate_bundle(facts_path, structured_path)


def test_bundle_requires_same_dataset(tmp_path) -> None:
    facts = demo_payload()
    structured = structured_payload()
    structured["dataset"] = "another-demo"
    facts_path = tmp_path / "demo_ground_truth.json"
    structured_path = tmp_path / "structured_query_ground_truth.json"
    facts_path.write_text(__import__("json").dumps(facts, ensure_ascii=False), encoding="utf-8")
    structured_path.write_text(
        __import__("json").dumps(structured, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(GroundTruthValidationError, match="dataset 必须一致"):
        load_and_validate_bundle(facts_path, structured_path)


def test_missing_database_environment_never_echoes_a_secret(monkeypatch) -> None:
    monkeypatch.delenv("GUOLIAN_TEST_DATABASE_URL", raising=False)
    with pytest.raises(DatabaseVerificationError) as raised:
        database_url_from_environment("GUOLIAN_TEST_DATABASE_URL")
    assert "GUOLIAN_TEST_DATABASE_URL" in str(raised.value)
    assert "password" not in str(raised.value).lower()


def test_invalid_database_url_never_echoes_credentials() -> None:
    secret_url = "unsupported+driver://demo:DO_NOT_ECHO@127.0.0.1/demo"
    with pytest.raises(DatabaseVerificationError) as raised:
        verify_database_ground_truth(structured_payload(), database_url=secret_url)
    assert "DO_NOT_ECHO" not in str(raised.value)
    assert secret_url not in str(raised.value)


def test_bundle_without_database_checks_needs_no_database_secret() -> None:
    payload = structured_payload()
    payload["questions"][0].pop("database_check")
    assert verify_database_ground_truth(payload) == []


def test_checked_in_structured_ground_truth_has_twenty_parameterized_checks() -> None:
    payload = json.loads(
        Path("demo/guolian/structured_query_ground_truth.json").read_text(encoding="utf-8")
    )
    validated = validate_structured_ground_truth(payload)
    assert len(validated["questions"]) == 20
    assert {item["database_check"]["result_mode"] for item in validated["questions"]} == {
        "scalar",
        "object",
        "rows",
    }
    for question in validated["questions"]:
        sql = question["database_check"]["sql"]
        parameters = question["database_check"]["parameters"]
        assert parameters
        assert set(re.findall(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", sql)) == set(parameters)


def test_checked_in_approval_scan_matches_ground_truth_and_zip_member() -> None:
    root = Path("demo/guolian")
    image_path = root / "扫描采购审批单（演示版）.jpg"
    old_supplier_assessment_digest = (
        "3e15e227c24834512f816b8e8c2fcbd9500bfbd5a49e9369a00f363dcedfe8ce"
    )

    with Image.open(image_path) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (1654, 2339)
    image_bytes = image_path.read_bytes()
    assert hashlib.sha256(image_bytes).hexdigest() != old_supplier_assessment_digest

    payload = validate_demo_ground_truth(
        json.loads((root / "demo_ground_truth.json").read_text(encoding="utf-8"))
    )
    fact = next(row for row in payload["facts"] if row["fact_id"] == "FACT-DEMO-025")
    assert fact["source"]["filename"] == image_path.name
    assert "PO-DEMO-2026-001" in fact["statement"]
    assert "360000" in fact["statement"]
    assert "审批通过" in fact["statement"]

    with ZipFile(root / "国联集团演示知识包.zip") as archive:
        assert len(archive.infolist()) == 10
        assert archive.read(image_path.name) == image_bytes
        assert "规则推演前提事实（演示版）.txt" in archive.namelist()


def test_guolian_mysql_and_postgresql_fixtures_have_matching_table_sets() -> None:
    postgres = Path("demo/guolian/db/postgresql.sql").read_text(encoding="utf-8")
    mysql = Path("demo/guolian/db/mysql.sql").read_text(encoding="utf-8")
    pattern = re.compile(r"CREATE TABLE\s+([a-z_]+)", re.IGNORECASE)
    postgres_tables = set(pattern.findall(postgres))
    mysql_tables = set(pattern.findall(mysql))
    assert postgres_tables == mysql_tables
    assert len(postgres_tables) >= 13
    assert "purchase_orders" in postgres_tables
    assert "supplier_contacts" in postgres_tables
    for sql in (postgres.lower(), mysql.lower()):
        assert "postgres_password" not in sql
        assert "mysql_password" not in sql
        assert "://" not in sql


def test_guolian_compose_uses_runtime_password_and_private_host_allowlist() -> None:
    compose = Path("compose.guolian-demo.yaml").read_text(encoding="utf-8")
    assert compose.count("${GUOLIAN_DEMO_DATABASE_PASSWORD:?") == 2
    assert "${GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD:?" in compose
    assert "guolian-demo-postgres,guolian-demo-mysql" in compose
    assert "GUOLIAN_DEMO_POSTGRES_DATABASE" in compose
    assert "GUOLIAN_DEMO_MYSQL_DATABASE" in compose
    assert "GUOLIAN_DEMO_POSTGRES_PUBLISHED_PORT" in compose
    assert "GUOLIAN_DEMO_MYSQL_PUBLISHED_PORT" in compose
    assert "GUOLIAN_DEMO_POSTGRES_PORT" not in compose
    assert "GUOLIAN_DEMO_MYSQL_PORT" not in compose
    assert compose.count("restart: unless-stopped") == 2
    assert "ephemeral_" not in compose
