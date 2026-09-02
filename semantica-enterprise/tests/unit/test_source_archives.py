from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packages.semantica_adapter.ingest import _archive_payload, extract_media_payloads, ingest_source
from packages.semantica_adapter.parse import parse_document


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


def test_source_archive_media_members_become_independent_safe_payloads() -> None:
    result = _archive_payload(
        "fixture",
        [("meetings/集团会议.mp3", b"ID3-media"), ("notes/readme.txt", b"plain")],
    )

    media = extract_media_payloads(result, maximum_items=2)

    assert len(media) == 1
    assert media[0].content_type == "audio/mpeg"
    assert media[0].body == b"ID3-media"
    assert media[0].metadata["source_path"] == "meetings/集团会议.mp3"


def test_source_archive_media_item_limit_is_enforced() -> None:
    result = _archive_payload("fixture", [("a.mp4", b"a"), ("b.mp4", b"b")])

    with pytest.raises(ValueError, match="超过配置上限"):
        extract_media_payloads(result, maximum_items=1)


def test_primary_source_archive_delegates_media_without_duplicate_elements(tmp_path) -> None:
    result = _archive_payload(
        "fixture",
        [("meeting.mp3", b"not-decoded-in-primary"), ("notes.txt", "会议结论：推进项目。".encode())],
    )
    archive_path = tmp_path / "fixture.zip"
    archive_path.write_bytes(result.body)

    elements, summary = parse_document(
        archive_path,
        version_id="source-document",
        policy={"parser_type": "auto", "skip_archive_media": True},
        supplied_mime="application/zip",
    )

    assert any("推进项目" in item.text for item in elements)
    assert not any(item.element_type in {"audio", "transcript_segment"} for item in elements)
    delegated = [item for item in summary["members"] if item["path"] == "meeting.mp3"]
    assert delegated[0]["status"] == "delegated"


def test_media_only_source_archive_remains_a_valid_manifest(tmp_path) -> None:
    result = _archive_payload(
        "media-only",
        [("training/nexusone.mp4", b"not-decoded-in-primary")],
    )
    archive_path = tmp_path / "media-only.zip"
    archive_path.write_bytes(result.body)

    elements, summary = parse_document(
        archive_path,
        version_id="source-document",
        policy={"parser_type": "auto", "skip_archive_media": True},
        supplied_mime="application/zip",
    )

    assert len(elements) == 1
    assert elements[0].element_type == "attachment"
    assert elements[0].metadata["delegated_media"] is True
    assert "training/nexusone.mp4" in elements[0].text
    assert summary["members"][0]["status"] == "delegated"
    assert summary["delegated_media_manifest"] is True


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
