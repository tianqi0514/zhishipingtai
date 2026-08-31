from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from packages.semantica_adapter.ingest import _archive_payload, ingest_source


def test_source_archive_normalizes_remote_paths_and_keeps_content() -> None:
    result = _archive_payload(
        "fixture",
        [("../../unsafe.txt", b"first"), ("folder/../unsafe.txt", b"second")],
    )

    with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
        names = archive.namelist()
        assert all(".." not in name for name in names)
        assert all(not name.startswith("/") for name in names)
        assert sorted(archive.read(name) for name in names) == [b"first", b"second"]


def test_source_archive_bytes_are_deterministic_for_incremental_sync() -> None:
    files = [("nested/b.txt", b"second"), ("a.txt", b"first")]

    first = _archive_payload("fixture", files)
    second = _archive_payload("fixture", list(reversed(files)))

    assert first.body == second.body


def test_local_directory_connector_performs_real_recursive_read(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "fact.txt").write_text("NexusOne supports hybrid retrieval.", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "data.json").write_text('{"priority": 1}', encoding="utf-8")

    settings = SimpleNamespace(source_mount_roots=str(tmp_path))
    with patch("packages.semantica_adapter.ingest.get_settings", return_value=settings):
        result = ingest_source(
            source_type="local_dir",
            source_name="mounted-fixture",
            config={"path": str(root), "recursive": True},
        )

    with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
        assert sorted(archive.namelist()) == ["fact.txt", "nested/data.json"]
        assert b"hybrid retrieval" in archive.read("fact.txt")
    assert result.metadata["file_count"] == 2
