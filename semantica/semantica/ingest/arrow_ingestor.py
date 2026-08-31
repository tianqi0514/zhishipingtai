"""
Apache Arrow IPC Ingestion Module

This module provides dedicated Arrow IPC ingestion for local ``.arrow``,
``.feather``, and ``.ipc`` files.  It uses PyArrow when available so callers
can read selected columns, inspect schemas and file metadata, and process
record batches without unnecessary full-table copies.

Supported formats:
    - Arrow IPC File (``*.arrow``, ``*.ipc``)
    - Feather v1/v2 (``*.feather``) — Feather v2 is the Arrow IPC format

Example Usage:
    >>> from semantica.ingest import ArrowIngestor
    >>> ingestor = ArrowIngestor()
    >>> data = ingestor.ingest_file("events.arrow", columns=["id", "event_type"])
    >>> schema = ingestor.extract_schema("events.arrow")
    >>> metadata = ingestor.extract_metadata("events.feather")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    import pyarrow as pa
    import pyarrow.feather as pf
    import pyarrow.ipc as ipc

    ARROW_AVAILABLE = True
except (ImportError, OSError):
    pa = None  # type: ignore[assignment]
    pf = None  # type: ignore[assignment]
    ipc = None  # type: ignore[assignment]
    ARROW_AVAILABLE = False

from ..utils.exceptions import ProcessingError, ValidationError
from ..utils.logging import get_logger
from ..utils.progress_tracker import get_progress_tracker

# Extensions recognised as Arrow IPC / Feather files.
_ARROW_EXTENSIONS = {".arrow", ".feather", ".ipc"}


@dataclass
class ArrowData:
    """Arrow IPC ingestion result."""

    data: List[Dict[str, Any]]
    row_count: int
    columns: List[str]
    schema: Dict[str, Any]
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    ingested_at: datetime = field(default_factory=datetime.now)


class _ArrowReaderWrapper:
    """Unified wrapper for Arrow File, Stream, and Feather readers."""

    def __init__(
        self,
        reader_or_table: Any,
        is_stream: bool = False,
        is_table: bool = False,
    ):
        self.obj = reader_or_table
        self.is_stream = is_stream
        self.is_table = is_table

    @property
    def schema(self) -> Any:
        return self.obj.schema

    def iter_batches(self) -> Any:
        if self.is_table:
            for batch in self.obj.to_batches():
                yield batch
        elif self.is_stream:
            for batch in self.obj:
                yield batch
        else:
            for i in range(self.obj.num_record_batches):
                yield self.obj.get_batch(i)


class ArrowIngestor:
    """
    Dedicated Arrow IPC / Feather ingestion handler.

    Features:
        - Single Arrow IPC or Feather file ingestion
        - Selective column reads
        - Schema and file metadata extraction
        - Batch-aware reading with optional row limits
        - Memory-efficient iteration over record batches
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Initialize Arrow ingestor.

        Args:
            config: Optional configuration dictionary
            **kwargs: Additional configuration options

        Raises:
            ImportError: If pyarrow is not installed
        """
        if not ARROW_AVAILABLE:
            raise ImportError(
                "pyarrow is required for ArrowIngestor. "
                "Install it with: pip install pyarrow"
            )

        self.logger = get_logger("arrow_ingestor")
        self.config = config or {}
        self.config.update(kwargs)
        self.progress_tracker = get_progress_tracker()
        if not self.progress_tracker.enabled:
            self.progress_tracker.enabled = True

        self.logger.debug("Arrow ingestor initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(
        self,
        source: Union[str, Path],
        columns: Optional[Union[str, Sequence[str]]] = None,
        limit: Optional[int] = None,
        include_data: bool = True,
        **options,
    ) -> ArrowData:
        """
        Ingest an Arrow IPC or Feather file.

        Args:
            source: Arrow / Feather file path
            columns: Optional column name or names to read
            limit: Optional maximum number of rows to return
            include_data: If False, return schema and metadata without rows
            **options: Additional options

        Returns:
            ArrowData: Ingested data and metadata
        """
        return self.ingest_file(
            source,
            columns=columns,
            limit=limit,
            include_data=include_data,
            **options,
        )

    def ingest_file(
        self,
        file_path: Union[str, Path],
        columns: Optional[Union[str, Sequence[str]]] = None,
        limit: Optional[int] = None,
        include_data: bool = True,
        **options,
    ) -> ArrowData:
        """
        Ingest a single Arrow IPC or Feather file.

        Args:
            file_path: Path to Arrow / Feather file
            columns: Optional column name or names to read
            limit: Optional maximum number of rows to return
            include_data: If False, skip reading row data
            **options: Additional options

        Returns:
            ArrowData: Ingested data, schema, and metadata
        """
        file_path = Path(file_path)
        self._validate_file(file_path)

        if limit is not None and limit < 0:
            raise ValidationError("limit must be greater than or equal to 0")

        tracking_id = self.progress_tracker.start_tracking(
            file=str(file_path),
            module="ingest",
            submodule="ArrowIngestor",
            message=f"Ingesting Arrow: {file_path.name}",
        )

        try:
            reader = self._open_file(file_path)
            file_schema = reader.schema

            selected_columns = self._normalize_columns(
                columns,
                [field.name for field in file_schema],
            )

            if include_data:
                table, batch_info = self._read_batches_with_info(
                    reader, selected_columns, limit
                )
                data = table.to_pylist()
                schema = self._schema_to_dict(table.schema)
                result_columns = list(table.column_names)
                metadata = {
                    "format": "arrow",
                    "source_type": "file",
                    "file": str(file_path),
                    "file_size": file_path.stat().st_size,
                    "total_rows": sum(b["num_rows"] for b in batch_info),
                    "num_record_batches": len(batch_info),
                    "num_columns": len(file_schema),
                    "record_batches": batch_info,
                    "schema_metadata": self._decode_metadata_map(file_schema.metadata),
                    "returned_rows": len(data),
                    "selected_columns": result_columns,
                    "limit": limit,
                    "include_data": include_data,
                }
            else:
                metadata = self._file_metadata(file_path, reader)
                selected_schema = self._select_schema(file_schema, selected_columns)
                data = []
                schema = self._schema_to_dict(selected_schema)
                result_columns = [f.name for f in selected_schema]
                metadata.update(
                    {
                        "returned_rows": 0,
                        "selected_columns": result_columns,
                        "limit": limit,
                        "include_data": include_data,
                    }
                )

            self.progress_tracker.stop_tracking(
                tracking_id,
                status="completed",
                message=f"Ingested Arrow: {len(data)} rows",
            )

            self.logger.info(
                f"Arrow ingestion completed: {len(data)} row(s) from {file_path}"
            )

            return ArrowData(
                data=data,
                row_count=len(data),
                columns=result_columns,
                schema=schema,
                source=str(file_path),
                metadata=metadata,
            )

        except (ValidationError, ProcessingError):
            self.progress_tracker.stop_tracking(
                tracking_id, status="failed", message="Arrow ingestion failed"
            )
            raise
        except Exception as e:
            self.progress_tracker.stop_tracking(
                tracking_id, status="failed", message=str(e)
            )
            self.logger.error(f"Failed to ingest Arrow {file_path}: {e}")
            raise ProcessingError(f"Failed to ingest Arrow file: {e}") from e

    def extract_schema(self, source: Union[str, Path], **options) -> Dict[str, Any]:
        """
        Extract schema from an Arrow IPC or Feather file.

        Args:
            source: Arrow / Feather file path
            **options: Additional options

        Returns:
            dict: Schema with column names, types, nullability, and metadata
        """
        source_path = Path(source)
        self._validate_file(source_path)
        reader = self._open_file(source_path)
        return self._schema_to_dict(reader.schema)

    def extract_metadata(self, source: Union[str, Path], **options) -> Dict[str, Any]:
        """
        Extract Arrow file metadata without reading row data.

        Args:
            source: Arrow / Feather file path
            **options: Additional options

        Returns:
            dict: Row counts, record batches, column info, and file metadata
        """
        source_path = Path(source)
        self._validate_file(source_path)
        reader = self._open_file(source_path)
        return self._file_metadata(source_path, reader)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_file(self, file_path: Path) -> Any:
        """Open an Arrow IPC file, stream, or Feather file.

        Tries opening as a File first, then Stream, then Feather.
        """
        try:
            # Try opening as Arrow File (Random Access format)
            reader = ipc.open_file(str(file_path))
            return _ArrowReaderWrapper(reader, is_stream=False)
        except Exception as file_err:
            try:
                # Try opening as Arrow Stream
                reader = ipc.open_stream(str(file_path))
                return _ArrowReaderWrapper(reader, is_stream=True)
            except Exception as stream_err:
                try:
                    # Try opening as Feather file
                    table = pf.read_table(str(file_path))
                    return _ArrowReaderWrapper(table, is_table=True)
                except Exception as feather_err:
                    raise ProcessingError(
                        f"Failed to open Arrow file {file_path}: "
                        f"IPC file error: {file_err}. "
                        f"IPC stream error: {stream_err}. "
                        f"Feather error: {feather_err}."
                    ) from file_err

    def _read_batches_with_info(
        self,
        reader: Any,
        columns: Optional[List[str]],
        limit: Optional[int],
    ) -> tuple:
        """Single-pass read: returns (Table, batch_info_list).

        Collecting batch metadata here avoids a separate full-file scan before
        the data read, which would double I/O for large files with small limits.
        """
        batch_info: List[Dict[str, Any]] = []

        if limit == 0:
            return (
                pa.Table.from_batches(
                    [],
                    schema=self._select_schema(reader.schema, columns),
                ),
                batch_info,
            )

        batches: list = []
        remaining = limit  # None means "all rows"

        for i, batch in enumerate(reader.iter_batches()):
            if columns is not None:
                batch = pa.RecordBatch.from_arrays(
                    [batch.column(c) for c in columns],
                    names=columns,
                )

            if remaining is not None:
                if batch.num_rows > remaining:
                    batch = batch.slice(0, remaining)
                remaining -= batch.num_rows

            batch_info.append(
                {
                    "batch_index": i,
                    "num_rows": batch.num_rows,
                    "num_columns": batch.num_columns,
                }
            )
            batches.append(batch)

            if remaining is not None and remaining <= 0:
                break

        return (
            pa.Table.from_batches(
                batches,
                schema=self._select_schema(reader.schema, columns),
            ),
            batch_info,
        )

    def _validate_file(self, file_path: Path) -> None:
        """Validate a local Arrow / Feather file path."""
        if not file_path.exists():
            raise ValidationError(f"Arrow file not found: {file_path}")
        if not file_path.is_file():
            raise ValidationError(f"Path is not a file: {file_path}")
        if file_path.suffix.lower() not in _ARROW_EXTENSIONS:
            raise ValidationError(
                f"File is not an Arrow/Feather file: {file_path}. "
                f"Supported extensions: {', '.join(sorted(_ARROW_EXTENSIONS))}"
            )

    def _normalize_columns(
        self,
        columns: Optional[Union[str, Sequence[str]]],
        available_columns: Sequence[str],
    ) -> Optional[List[str]]:
        """Normalize and validate optional selected columns."""
        if columns is None:
            configured_columns = self.config.get("columns")
            if configured_columns is None:
                return None
            columns = configured_columns

        if isinstance(columns, str):
            normalized = [columns]
        else:
            normalized = list(columns)

        missing = [col for col in normalized if col not in available_columns]
        if missing:
            raise ValidationError(
                "Column(s) not found in Arrow schema: "
                f"{', '.join(missing)}. Available columns: "
                f"{', '.join(available_columns)}"
            )
        return normalized

    def _select_schema(self, schema: Any, columns: Optional[List[str]]) -> Any:
        """Return schema limited to selected columns when provided."""
        if columns is None:
            return schema
        fields = [schema.field(column) for column in columns]
        return pa.schema(fields, metadata=schema.metadata)

    def _schema_to_dict(self, schema: Any) -> Dict[str, Any]:
        """Convert PyArrow schema to serializable metadata."""
        fields = []
        for schema_field in schema:
            fields.append(
                {
                    "name": schema_field.name,
                    "type": str(schema_field.type),
                    "nullable": schema_field.nullable,
                    "metadata": self._decode_metadata_map(schema_field.metadata),
                }
            )

        return {
            "columns": [field_info["name"] for field_info in fields],
            "fields": fields,
            "metadata": self._decode_metadata_map(schema.metadata),
        }

    def _file_metadata(self, file_path: Path, reader: Any) -> Dict[str, Any]:
        """Extract metadata for a single Arrow IPC / Feather file."""
        total_rows = 0
        batch_info: List[Dict[str, Any]] = []

        # If it's a stream, open a new one to avoid exhausting the caller's reader
        meta_reader = reader
        if reader.is_stream:
            meta_reader = self._open_file(file_path)

        for i, batch in enumerate(meta_reader.iter_batches()):
            total_rows += batch.num_rows
            batch_info.append(
                {
                    "batch_index": i,
                    "num_rows": batch.num_rows,
                    "num_columns": batch.num_columns,
                }
            )

        return {
            "format": "arrow",
            "source_type": "file",
            "file": str(file_path),
            "file_size": file_path.stat().st_size,
            "total_rows": total_rows,
            "num_record_batches": len(batch_info),
            "num_columns": len(reader.schema),
            "record_batches": batch_info,
            "schema_metadata": self._decode_metadata_map(reader.schema.metadata),
        }

    def _decode_metadata_map(
        self, metadata: Optional[Dict[Any, Any]]
    ) -> Dict[str, str]:
        """Decode PyArrow metadata bytes to strings."""
        if not metadata:
            return {}

        decoded = {}
        for key, value in metadata.items():
            decoded[self._decode_metadata_value(key)] = self._decode_metadata_value(
                value
            )
        return decoded

    def _decode_metadata_value(self, value: Any) -> str:
        """Decode a metadata key or value."""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
