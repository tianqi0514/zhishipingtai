from __future__ import annotations

from packages.semantica_adapter.parse import _elements_from_result


def _snapshot(rows: list[dict]) -> dict:
    return {
        "data": {
            "schema": {
                "tables": [{
                    "name": "customers",
                    "primary_keys": ["id"],
                }],
            },
            "tables": {
                "customers": {
                    "columns": [
                        {"name": "id"}, {"name": "name"}, {"name": "phone"}, {"name": "password"},
                    ],
                    "rows": rows,
                    "row_count": len(rows),
                },
            },
        },
    }


def test_database_snapshot_creates_table_and_stable_row_elements() -> None:
    context = {
        "source_id": "source-1",
        "schema_version_id": "schema-1",
        "schema_fingerprint": "a" * 64,
        "default_schema": "public",
        "sync_time": "2026-09-01T10:00:00+00:00",
    }
    first = _elements_from_result(
        "document-identity",
        "json",
        _snapshot([{"id": 1, "name": "甲公司", "phone": "13800138000", "password": "not-persisted"}]),
        context,
    )
    second = _elements_from_result(
        "document-identity",
        "json",
        _snapshot([{"id": 1, "name": "甲公司（更新）", "phone": "13800138000", "password": "changed"}]),
        context,
    )
    assert [item.element_type for item in first] == ["table", "record"]
    assert first[1].element_id == second[1].element_id
    assert first[1].text != second[1].text
    assert "not-persisted" not in first[1].text
    assert first[1].metadata["row"]["phone"] == "138****8000"
    assert first[1].metadata["object_id"] == "public.customers"
    assert first[1].metadata["stable_identity"] is True


def test_database_snapshot_marks_rows_without_primary_key_unstable() -> None:
    payload = _snapshot([{"id": 1, "name": "甲公司", "phone": None, "password": "secret"}])
    payload["data"]["schema"]["tables"][0]["primary_keys"] = []
    elements = _elements_from_result("document-identity", "json", payload, {"source_id": "source-1"})
    record = elements[1]
    assert record.metadata["unstable_identity"] is True
    assert record.metadata["primary_key"] == []
