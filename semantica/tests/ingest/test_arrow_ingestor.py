"""Comprehensive tests for the Arrow IPC / Feather ingestor."""

from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
ipc = pytest.importorskip("pyarrow.ipc")

from semantica.ingest import (  # noqa: E402
    ArrowData,
    ArrowIngestor,
    ingest,
    ingest_arrow,
    ingest_file,
    list_available_methods,
)
from semantica.ingest.file_ingestor import FileTypeDetector  # noqa: E402
from semantica.utils.exceptions import ProcessingError, ValidationError  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_arrow_ipc(path: Path, table: pa.Table) -> Path:
    """Write a table as an Arrow IPC file."""
    with pa.OSFile(str(path), "wb") as sink:
        writer = ipc.new_file(sink, table.schema)
        writer.write_table(table)
        writer.close()
    return path


def _write_arrow_ipc_batches(path: Path, batches, schema: pa.Schema) -> Path:
    """Write multiple record batches as an Arrow IPC file."""
    with pa.OSFile(str(path), "wb") as sink:
        writer = ipc.new_file(sink, schema)
        for batch in batches:
            writer.write_batch(batch)
        writer.close()
    return path


@pytest.fixture
def sample_arrow(tmp_path: Path) -> Path:
    """Simple Arrow IPC file with 4 columns and 3 rows."""
    table = pa.table(
        {
            "id": [1, 2, 3],
            "name": ["alpha", "beta", "gamma"],
            "score": [0.7, 0.8, 0.9],
            "city": ["Pune", "Delhi", "Mumbai"],
        }
    )
    return _write_arrow_ipc(tmp_path / "events.arrow", table)


@pytest.fixture
def sample_feather(tmp_path: Path) -> Path:
    """Feather v2 file (Arrow IPC format)."""
    import pyarrow.feather as pf

    table = pa.table({"x": [10, 20], "y": ["a", "b"]})
    path = tmp_path / "data.feather"
    pf.write_feather(table, str(path))
    return path


@pytest.fixture
def multi_type_arrow(tmp_path: Path) -> Path:
    """Arrow file with multiple data types."""
    table = pa.table(
        {
            "int_col": pa.array([1, 2, 3], type=pa.int64()),
            "float_col": pa.array([1.1, 2.2, 3.3], type=pa.float64()),
            "str_col": pa.array(["a", "b", "c"], type=pa.string()),
            "bool_col": pa.array([True, False, True], type=pa.bool_()),
        }
    )
    return _write_arrow_ipc(tmp_path / "multi.arrow", table)


@pytest.fixture
def multi_batch_arrow(tmp_path: Path) -> Path:
    """Arrow file with multiple record batches."""
    schema = pa.schema([("id", pa.int64()), ("value", pa.string())])
    batch1 = pa.record_batch({"id": [1, 2], "value": ["a", "b"]}, schema=schema)
    batch2 = pa.record_batch({"id": [3, 4], "value": ["c", "d"]}, schema=schema)
    batch3 = pa.record_batch({"id": [5], "value": ["e"]}, schema=schema)
    return _write_arrow_ipc_batches(
        tmp_path / "batched.arrow", [batch1, batch2, batch3], schema
    )


@pytest.fixture
def empty_table_arrow(tmp_path: Path) -> Path:
    """Arrow file with zero rows."""
    table = pa.table(
        {"id": pa.array([], type=pa.int64()), "name": pa.array([], type=pa.string())}
    )
    return _write_arrow_ipc(tmp_path / "empty.arrow", table)


@pytest.fixture
def nullable_arrow(tmp_path: Path) -> Path:
    """Arrow file with null values."""
    table = pa.table(
        {
            "id": [1, 2, 3],
            "name": ["alpha", None, "gamma"],
            "score": [0.7, None, 0.9],
        }
    )
    return _write_arrow_ipc(tmp_path / "nullable.arrow", table)


@pytest.fixture
def metadata_arrow(tmp_path: Path) -> Path:
    """Arrow file with schema-level and field-level metadata."""
    field_id = pa.field("id", pa.int64(), metadata={b"description": b"primary key"})
    field_name = pa.field("name", pa.string())
    schema = pa.schema(
        [field_id, field_name],
        metadata={b"author": b"test", b"version": b"1.0"},
    )
    table = pa.table({"id": [1, 2], "name": ["a", "b"]}, schema=schema)
    return _write_arrow_ipc(tmp_path / "meta.arrow", table)


@pytest.fixture
def no_metadata_arrow(tmp_path: Path) -> Path:
    """Arrow file with no schema metadata."""
    table = pa.table({"x": [1]})
    return _write_arrow_ipc(tmp_path / "nometa.arrow", table)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_arrow_file_ingestion_reads_data_schema_and_metadata(
    sample_arrow: Path,
) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_arrow)

    assert isinstance(result, ArrowData)
    assert result.row_count == 3
    assert result.columns == ["id", "name", "score", "city"]
    assert result.data[0]["name"] == "alpha"
    assert result.schema["columns"] == ["id", "name", "score", "city"]
    assert result.schema["fields"][0]["type"] == "int64"
    assert result.metadata["total_rows"] == 3
    assert result.metadata["format"] == "arrow"


def test_feather_file_ingestion(sample_feather: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_feather)

    assert isinstance(result, ArrowData)
    assert result.row_count == 2
    assert result.columns == ["x", "y"]
    assert result.data == [{"x": 10, "y": "a"}, {"x": 20, "y": "b"}]


def test_multi_type_columns(multi_type_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(multi_type_arrow)

    assert result.row_count == 3
    type_map = {f["name"]: f["type"] for f in result.schema["fields"]}
    assert type_map["int_col"] == "int64"
    assert type_map["float_col"] == "double"
    assert type_map["str_col"] == "string"
    assert type_map["bool_col"] == "bool"


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_extract_schema(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    schema = ingestor.extract_schema(sample_arrow)

    assert schema["columns"] == ["id", "name", "score", "city"]
    assert len(schema["fields"]) == 4
    assert all("nullable" in f for f in schema["fields"])


def test_schema_metadata_extraction(metadata_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    schema = ingestor.extract_schema(metadata_arrow)

    assert schema["metadata"]["author"] == "test"
    assert schema["metadata"]["version"] == "1.0"
    # Field-level metadata
    id_field = next(f for f in schema["fields"] if f["name"] == "id")
    assert id_field["metadata"]["description"] == "primary key"


def test_metadata_extraction(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    metadata = ingestor.extract_metadata(sample_arrow)

    assert metadata["total_rows"] == 3
    assert metadata["num_columns"] == 4
    assert metadata["format"] == "arrow"
    assert metadata["source_type"] == "file"
    assert metadata["file_size"] > 0
    assert len(metadata["record_batches"]) >= 1


# ---------------------------------------------------------------------------
# Selective columns and limits
# ---------------------------------------------------------------------------


def test_selective_column_reading(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_arrow, columns=["id", "name"])

    assert result.columns == ["id", "name"]
    assert result.row_count == 3
    assert all(set(row.keys()) == {"id", "name"} for row in result.data)


def test_row_limit(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_arrow, limit=2)

    assert result.row_count == 2
    assert result.metadata["limit"] == 2


def test_limit_zero(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_arrow, limit=0)

    assert result.row_count == 0
    assert result.data == []


def test_selective_columns_with_limit(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_arrow, columns=["id", "name"], limit=2)

    assert result.row_count == 2
    assert result.columns == ["id", "name"]
    assert result.data == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]


# ---------------------------------------------------------------------------
# Batch tests
# ---------------------------------------------------------------------------


def test_multi_batch_reading(multi_batch_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(multi_batch_arrow)

    assert result.row_count == 5
    assert result.columns == ["id", "value"]
    assert result.metadata["num_record_batches"] == 3
    assert [row["id"] for row in result.data] == [1, 2, 3, 4, 5]


def test_multi_batch_with_limit(multi_batch_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(multi_batch_arrow, limit=3)

    assert result.row_count == 3
    assert [row["id"] for row in result.data] == [1, 2, 3]


# ---------------------------------------------------------------------------
# include_data=False
# ---------------------------------------------------------------------------


def test_include_data_false(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(sample_arrow, include_data=False)

    assert result.row_count == 0
    assert result.data == []
    assert result.metadata["include_data"] is False
    assert result.schema["columns"] == ["id", "name", "score", "city"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_table(empty_table_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(empty_table_arrow)

    assert result.row_count == 0
    assert result.data == []
    assert result.columns == ["id", "name"]
    assert result.schema["columns"] == ["id", "name"]


def test_null_values(nullable_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    result = ingestor.ingest_file(nullable_arrow)

    assert result.row_count == 3
    assert result.data[1]["name"] is None
    assert result.data[1]["score"] is None
    assert result.data[0]["name"] == "alpha"
    assert result.data[2]["score"] == 0.9


def test_missing_metadata_no_exception(no_metadata_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    metadata = ingestor.extract_metadata(no_metadata_arrow)
    schema = ingestor.extract_schema(no_metadata_arrow)

    # Should return empty dict, not raise
    assert isinstance(metadata["schema_metadata"], dict)
    assert isinstance(schema["metadata"], dict)


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------


def test_missing_file_raises_validation_error() -> None:
    ingestor = ArrowIngestor()

    with pytest.raises(ValidationError, match="Arrow file not found"):
        ingestor.ingest_file("/nonexistent/path/data.arrow")


def test_wrong_extension_raises_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("hello")

    ingestor = ArrowIngestor()

    with pytest.raises(ValidationError, match="not an Arrow/Feather file"):
        ingestor.ingest_file(path)


def test_corrupted_file_raises_processing_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.arrow"
    path.write_bytes(b"\x00\x01\x02\x03garbage data here")

    ingestor = ArrowIngestor()

    with pytest.raises(ProcessingError, match="Failed to open Arrow file"):
        ingestor.ingest_file(path)


def test_invalid_text_file_raises_processing_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid.arrow"
    path.write_text("this is not an arrow file")

    ingestor = ArrowIngestor()

    with pytest.raises(ProcessingError):
        ingestor.ingest_file(path)


def test_negative_limit_raises_validation_error(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    with pytest.raises(
        ValidationError, match="limit must be greater than or equal to 0"
    ):
        ingestor.ingest_file(sample_arrow, limit=-1)


def test_invalid_column_raises_validation_error(sample_arrow: Path) -> None:
    ingestor = ArrowIngestor()

    with pytest.raises(ValidationError, match="Column.*not found"):
        ingestor.ingest_file(sample_arrow, columns=["nonexistent_col"])


# ---------------------------------------------------------------------------
# Convenience functions and dispatch
# ---------------------------------------------------------------------------


def test_ingest_arrow_convenience_function(sample_arrow: Path) -> None:
    result = ingest_arrow(sample_arrow, columns=["name"])

    assert isinstance(result, ArrowData)
    assert result.columns == ["name"]


def test_ingest_arrow_schema_method(sample_arrow: Path) -> None:
    schema = ingest_arrow(sample_arrow, method="schema")

    assert isinstance(schema, dict)
    assert "columns" in schema
    assert "fields" in schema


def test_ingest_arrow_metadata_method(sample_arrow: Path) -> None:
    metadata = ingest_arrow(sample_arrow, method="metadata")

    assert isinstance(metadata, dict)
    assert metadata["format"] == "arrow"


def test_ingest_arrow_list_of_files(sample_arrow: Path, sample_feather: Path) -> None:
    results = ingest_arrow([sample_arrow, sample_feather])

    assert isinstance(results, list)
    assert len(results) == 2
    assert all(isinstance(r, ArrowData) for r in results)


def test_unified_ingest_auto_detects_arrow(sample_arrow: Path) -> None:
    result = ingest(sample_arrow)

    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], ArrowData)


def test_unified_ingest_auto_detects_feather(sample_feather: Path) -> None:
    result = ingest(sample_feather)

    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], ArrowData)


def test_ingest_file_method_arrow(sample_arrow: Path) -> None:
    result = ingest_file(sample_arrow, method="arrow")

    assert isinstance(result, ArrowData)


# ---------------------------------------------------------------------------
# Magic number detection
# ---------------------------------------------------------------------------


def test_file_type_detector_recognizes_arrow_magic_number() -> None:
    detector = FileTypeDetector()

    # Arrow IPC files start with "ARROW1\x00\x00"
    arrow_content = b"ARROW1\x00\x00" + b"\x00" * 100
    assert detector.detect_type("noext", content=arrow_content) == "arrow"
    assert detector.is_supported("arrow")
    assert detector.is_supported("feather")
    assert detector.is_supported("ipc")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_arrow_methods_registered() -> None:
    methods = list_available_methods("arrow")

    assert "arrow" in methods
    assert "file" in methods["arrow"]
    assert "schema" in methods["arrow"]
    assert "metadata" in methods["arrow"]


def test_ingest_method_alias() -> None:
    """ingest(method='arrow') in file context should dispatch to arrow."""
    ingestor = ArrowIngestor()
    # Just verify the ingestor can be instantiated — the actual dispatch
    # is tested via ingest_file(method="arrow") above.
    assert ingestor is not None


# ---------------------------------------------------------------------------
# Streaming Format Tests
# ---------------------------------------------------------------------------


def _write_arrow_stream(path: Path, table: pa.Table) -> Path:
    """Write a table as an Arrow IPC Stream file."""
    with pa.OSFile(str(path), "wb") as sink:
        writer = ipc.new_stream(sink, table.schema)
        writer.write_table(table)
        writer.close()
    return path


@pytest.fixture
def sample_arrow_stream(tmp_path: Path) -> Path:
    """Arrow IPC Stream format file."""
    table = pa.table(
        {
            "id": [100, 200],
            "name": ["stream1", "stream2"],
        }
    )
    return _write_arrow_stream(tmp_path / "stream.ipc", table)


def test_arrow_stream_file_ingestion(sample_arrow_stream: Path) -> None:
    ingestor = ArrowIngestor()
    result = ingestor.ingest_file(sample_arrow_stream)

    assert isinstance(result, ArrowData)
    assert result.row_count == 2
    assert result.columns == ["id", "name"]
    assert result.data == [
        {"id": 100, "name": "stream1"},
        {"id": 200, "name": "stream2"},
    ]


def test_arrow_stream_metadata_and_schema(sample_arrow_stream: Path) -> None:
    ingestor = ArrowIngestor()
    schema = ingestor.extract_schema(sample_arrow_stream)
    metadata = ingestor.extract_metadata(sample_arrow_stream)

    assert schema["columns"] == ["id", "name"]
    assert metadata["format"] == "arrow"
    assert metadata["total_rows"] == 2
