from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.structured_schemas import DataPreviewRequest, SemanticQueryIR
from packages.platform.database import Base
from packages.platform.models import DataSourceSchemaVersion, KnowledgeSpace, SourceConnector, Tenant
from packages.platform.structured_data import _mask, persist_discovery, schema_diff, sensitive_suggestion


def _catalog(columns: list[tuple[str, str]], *, primary_key: list[str] | None = None) -> dict:
    return {
        "objects": [{
            "id": "public.customers",
            "schema": "public",
            "name": "customers",
            "columns": [
                {"id": f"public.customers.{name}", "name": name, "type": type_name, "type_family": "integer" if "INT" in type_name else "string", "nullable": False, "comment": ""}
                for name, type_name in columns
            ],
            "primary_key": primary_key or [],
            "foreign_keys": [],
        }]
    }


@pytest.mark.parametrize("name", ["password", "api_key", "access_token", "private_key", "user_secret"])
def test_secret_columns_are_blocked(name: str) -> None:
    assert sensitive_suggestion(name)[0] == "blocked"


@pytest.mark.parametrize(
    ("name", "rule"),
    [("mobile_phone", "phone"), ("email", "email"), ("id_card", "id_card"), ("bank_card", "bank_card")],
)
def test_personal_data_columns_are_masked(name: str, rule: str) -> None:
    assert sensitive_suggestion(name) == ("masked", rule)


def test_server_side_masking_never_returns_original_value() -> None:
    assert _mask("13800138000", "phone") == "138****8000"
    assert _mask("person@example.com", "email") == "p***@example.com"
    assert "320101" not in _mask("320101199001011234", "id_card")


def test_schema_diff_detects_breaking_changes_and_rename_candidate() -> None:
    previous = _catalog([("id", "INTEGER"), ("customer_name", "VARCHAR")], primary_key=["id"])
    current = _catalog([("id", "BIGINT"), ("name", "VARCHAR")], primary_key=["id"])
    diff = schema_diff(previous, current)
    assert diff["breaking"] is True
    assert diff["removed_columns"] == ["public.customers.customer_name"]
    assert diff["added_columns"] == ["public.customers.name"]
    assert diff["rename_candidates"] == [{"from": "public.customers.customer_name", "to": "public.customers.name"}]
    assert diff["type_changes"][0]["compatible"] is True


def test_preview_request_rejects_raw_sql_and_too_many_filters() -> None:
    with pytest.raises(ValidationError):
        DataPreviewRequest.model_validate({"object_id": "customers", "sql": "DROP TABLE customers"})
    with pytest.raises(ValidationError):
        DataPreviewRequest.model_validate({
            "object_id": "customers",
            "filters": [{"column_id": "c.id", "operator": "eq", "value": index} for index in range(11)],
        })


def test_query_ir_rejects_raw_sql_and_physical_names() -> None:
    with pytest.raises(ValidationError):
        SemanticQueryIR.model_validate({
            "from_entity": {"binding": "c", "entity_id": "customer"},
            "select": [{"expression": {"kind": "literal", "value": 1}}],
            "sql": "SELECT * FROM customers",
        })
    with pytest.raises(ValidationError):
        SemanticQueryIR.model_validate({
            "from_entity": {"binding": "c", "entity_id": "customer", "table": "customers"},
            "select": [{"expression": {"kind": "literal", "value": 1}}],
        })


def test_schema_can_return_to_a_historical_fingerprint_with_a_new_version() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    first_catalog = _catalog([("id", "INTEGER"), ("status", "VARCHAR")], primary_key=["id"])
    drifted_catalog = _catalog([("id", "INTEGER"), ("status_drift", "VARCHAR")], primary_key=["id"])
    first_catalog["schema_fingerprint"] = "a" * 64
    drifted_catalog["schema_fingerprint"] = "b" * 64
    with Session(engine) as db:
        tenant = Tenant(code="tenant", name="Tenant")
        db.add(tenant)
        db.flush()
        space = KnowledgeSpace(tenant_id=tenant.id, code="space", name="Space")
        db.add(space)
        db.flush()
        source = SourceConnector(
            tenant_id=tenant.id,
            space_id=space.id,
            name="Fixture",
            source_type="database",
        )
        db.add(source)
        db.flush()

        first, _ = persist_discovery(db, source, first_catalog)
        drifted, _ = persist_discovery(db, source, drifted_catalog)
        restored, created = persist_discovery(db, source, first_catalog)
        db.commit()

        versions = list(db.scalars(select(DataSourceSchemaVersion).order_by(DataSourceSchemaVersion.version_number)))
        assert created is True
        assert [item.schema_fingerprint for item in versions] == ["a" * 64, "b" * 64, "a" * 64]
        assert [item.version_number for item in versions] == [1, 2, 3]
        assert first.status == "superseded"
        assert drifted.status == "superseded"
        assert restored.status == "current"
