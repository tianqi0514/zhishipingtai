from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock, patch

from apps.api.schemas import SourceCreate
from packages.semantica_adapter.ingest import ingest_source


SPACE_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    ("source_type", "config"),
    [
        ("web", {"url": "https://example.com"}),
        ("rest", {"url": "https://example.com/api", "method": "GET"}),
        ("rss", {"url": "https://example.com/feed.xml"}),
        ("sitemap", {"url": "https://example.com/sitemap.xml", "max_urls": 10}),
        ("git", {"url": "https://github.com/semantica-agi/semantica", "depth": 1}),
        ("database", {"dialect": "postgresql", "host": "postgres", "database": "semantica", "username": "reader"}),
        ("email", {"protocol": "imap", "server": "imap.example.com", "username": "reader"}),
        ("mcp", {"url": "https://example.com/mcp", "server_name": "knowledge"}),
        ("google_drive", {"folder_id": "root", "recursive": True}),
        ("mongodb", {"host": "mongo.example.com", "database": "knowledge", "collection": "docs"}),
        ("elasticsearch", {"url": "https://search.example.com", "index": "knowledge"}),
        ("opensearch", {"url": "https://search.example.com", "index": "knowledge"}),
        ("duckdb", {"path": "/app/data/sources/data.csv"}),
        ("parquet", {"path": "/app/data/sources/data.parquet"}),
        ("arrow", {"path": "/app/data/sources/data.arrow"}),
        ("huggingface", {"dataset": "lhoestq/demo1", "split": "train"}),
        ("stream", {"stream_type": "rabbitmq", "host": "rabbitmq", "queue": "knowledge"}),
        ("snowflake", {"account": "acme", "username": "reader", "warehouse": "wh", "database": "db", "table": "docs"}),
        ("databricks", {"host": "https://acme.cloud.databricks.com", "http_path": "/sql/1.0/warehouses/x", "table": "docs"}),
        ("local_dir", {"path": "/app/data/sources", "recursive": True}),
        ("s3", {"endpoint": "minio:9000", "bucket": "knowledge", "access_key": "reader"}),
        ("object_prefix", {"endpoint": "minio:9000", "bucket": "knowledge", "access_key": "reader", "prefix": "docs/"}),
        ("sftp", {"host": "sftp.example.com", "username": "reader", "path": "/docs"}),
        ("ftp", {"host": "ftp.example.com", "path": "/docs"}),
        ("ftps", {"host": "ftp.example.com", "username": "reader", "path": "/docs"}),
        ("webdav", {"url": "https://dav.example.com/docs/", "username": "reader"}),
        ("smb", {"server": "files.example.com", "share": "knowledge", "username": "reader"}),
        ("onedrive", {"drive_id": "me", "path": "Knowledge"}),
        ("sharepoint", {"site_id": "tenant,site", "path": "Shared Documents"}),
    ],
)
def test_all_supported_source_types_validate(source_type: str, config: dict) -> None:
    source = SourceCreate(space_id=SPACE_ID, name="测试", source_type=source_type, config=config)
    assert source.source_type == source_type


def test_database_password_cannot_be_embedded_in_regular_config() -> None:
    # URL-style credentials remain rejected for all URL-based connectors.
    with pytest.raises(ValidationError):
        SourceCreate(
            space_id=SPACE_ID,
            name="不安全",
            source_type="rest",
            config={"url": "https://user:password@example.com/api"},
        )


def test_native_git_transport_is_limited_to_git_connectors() -> None:
    git_source = SourceCreate(
        space_id=SPACE_ID,
        name="内网 Git",
        source_type="git",
        config={"url": "git://source-fixture:9418/guolian-demo.git", "depth": 1},
    )
    assert git_source.config["url"].startswith("git://")
    with pytest.raises(ValidationError, match="HTTP 或 HTTPS"):
        SourceCreate(
            space_id=SPACE_ID,
            name="错误 REST",
            source_type="rest",
            config={"url": "git://source-fixture:9418/guolian-demo.git"},
        )


def test_schedule_interval_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SourceCreate(
            space_id=SPACE_ID,
            name="错误周期",
            source_type="rss",
            config={"url": "https://example.com/feed.xml", "schedule_minutes": 10081},
        )


def test_sensitive_connector_values_must_use_encrypted_secret_field() -> None:
    with pytest.raises(ValidationError):
        SourceCreate(
            space_id=SPACE_ID,
            name="不安全对象存储",
            source_type="s3",
            config={
                "endpoint": "s3.example.com",
                "bucket": "knowledge",
                "access_key": "reader",
                "password": "must-not-live-in-config",
            },
        )


def test_sitemap_does_not_pass_empty_regex_patterns() -> None:
    ingestor = MagicMock()
    ingestor.crawl_sitemap.return_value = []
    with (
        patch("semantica.ingest.web_ingestor.WebIngestor", return_value=ingestor),
        patch("packages.semantica_adapter.ingest._assert_network_target"),
    ):
        result = ingest_source(
            source_type="sitemap",
            source_name="站点地图",
            config={"url": "https://example.com/sitemap.xml", "max_urls": 2},
        )
    _, kwargs = ingestor.crawl_sitemap.call_args
    assert "pattern" not in kwargs
    assert "exclude_pattern" not in kwargs
    assert result.metadata["page_count"] == 0


def test_sitemap_snapshot_is_stable_across_fetch_times_and_url_order() -> None:
    first_time = datetime(2026, 9, 6, 9, 0, 0)
    pages = [
        SimpleNamespace(url="https://example.com/b", title="B", text="第二页", fetched_at=first_time),
        SimpleNamespace(url="https://example.com/a", title="A", text="第一页", fetched_at=first_time),
    ]
    ingestor = MagicMock()
    ingestor.crawl_sitemap.side_effect = [
        pages,
        [
            SimpleNamespace(
                url=page.url,
                title=page.title,
                text=page.text,
                fetched_at=page.fetched_at + timedelta(minutes=5),
            )
            for page in reversed(pages)
        ],
    ]
    with (
        patch("semantica.ingest.web_ingestor.WebIngestor", return_value=ingestor),
        patch("packages.semantica_adapter.ingest._assert_network_target"),
    ):
        first = ingest_source(
            source_type="sitemap",
            source_name="站点地图",
            config={"url": "https://example.com/sitemap.xml", "max_urls": 2},
        )
        second = ingest_source(
            source_type="sitemap",
            source_name="站点地图",
            config={"url": "https://example.com/sitemap.xml", "max_urls": 2},
        )

    assert first.body == second.body
    assert b"fetched_at" not in first.body
    assert first.body.index(b"https://example.com/a") < first.body.index(b"https://example.com/b")


def test_git_snapshot_is_stable_across_clone_paths_mtimes_and_file_order(tmp_path: Path) -> None:
    clone_a = tmp_path / "clone-a"
    clone_b = tmp_path / "clone-b"
    for clone in (clone_a, clone_b):
        (clone / "config").mkdir(parents=True)
        (clone / "README.md").write_text("NexusOne", encoding="utf-8")
        (clone / "config" / "project.yaml").write_text("project: demo", encoding="utf-8")

    def repository_result(clone: Path, modified: datetime, *, reverse: bool) -> dict:
        files = [
            {
                "path": str(clone / "README.md"),
                "name": "README.md",
                "language": "markdown",
                "content": "NexusOne",
                "size": 8,
                "lines": 1,
                "metadata": {"extension": ".md", "size_bytes": 8, "modified": modified},
            },
            {
                "path": str(clone / "config" / "project.yaml"),
                "name": "project.yaml",
                "language": "yaml",
                "content": "project: demo",
                "size": 13,
                "lines": 1,
                "metadata": {"extension": ".yaml", "size_bytes": 13, "modified": modified},
            },
        ]
        if reverse:
            files.reverse()
        return {
            "repository_info": {
                "url": "https://example.com/demo.git",
                "branches": ["release", "main"] if reverse else ["main", "release"],
                "tags": ["v2", "v1"] if reverse else ["v1", "v2"],
                "latest_commit": {"hash": "abcdef0", "date": "2026-09-01T09:00:00"},
            },
            "code_files": files,
            "commits": [],
            "structure": {"file_types": {".yaml": 1, ".md": 1}},
            # Semantica's current repository-wide metrics include volatile
            # .git internals; make them differ like two fresh shallow clones.
            "metrics": {
                "total_files": 30 if reverse else 31,
                "total_lines": 940 if reverse else 970,
                "files_by_language": {"unknown": 28, "yaml": 1, "markdown": 1},
                "lines_by_language": {
                    "unknown": 900 if reverse else 930,
                    "yaml": 1,
                    "markdown": 1,
                },
            },
            "temp_path": str(clone),
        }

    ingestor = MagicMock()
    ingestor.ingest_repository.side_effect = [
        repository_result(clone_a, datetime(2026, 9, 6, 9, 0, 0), reverse=False),
        repository_result(clone_b, datetime(2026, 9, 6, 9, 5, 0), reverse=True),
    ]
    with (
        patch("semantica.ingest.repo_ingestor.RepoIngestor", return_value=ingestor),
        patch("packages.semantica_adapter.ingest._assert_network_target"),
    ):
        first = ingest_source(
            source_type="git",
            source_name="项目仓库",
            config={"url": "https://example.com/demo.git", "include_extensions": ["md", "yaml"]},
        )
        second = ingest_source(
            source_type="git",
            source_name="项目仓库",
            config={"url": "https://example.com/demo.git", "include_extensions": ["md", "yaml"]},
        )

    assert first.body == second.body
    assert str(clone_a).encode() not in first.body
    assert str(clone_b).encode() not in second.body
    assert b"modified" not in first.body
    assert b"config/project.yaml" in first.body
    assert b'"total_files": 2' in first.body
    assert b'"unknown"' not in first.body
    assert ingestor.cleanup.call_count == 2


def test_git_snapshot_digest_changes_when_repository_content_changes(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    ingestor = MagicMock()

    def repository_result(content: str) -> dict:
        return {
            "repository_info": {"url": "https://example.com/demo.git", "branches": ["main"]},
            "code_files": [
                {
                    "path": str(clone / "README.md"),
                    "name": "README.md",
                    "language": "markdown",
                    "content": content,
                    "size": len(content.encode("utf-8")),
                    "lines": 1,
                    "metadata": {"extension": ".md", "modified": datetime.now()},
                }
            ],
            "commits": [],
            "structure": {"total_files": 1},
            "metrics": {"total_files": 99, "total_lines": 999},
            "temp_path": str(clone),
        }

    ingestor.ingest_repository.side_effect = [
        repository_result("NexusOne"),
        repository_result("NexusOne 2.0"),
    ]
    with (
        patch("semantica.ingest.repo_ingestor.RepoIngestor", return_value=ingestor),
        patch("packages.semantica_adapter.ingest._assert_network_target"),
    ):
        first = ingest_source(
            source_type="git",
            source_name="项目仓库",
            config={"url": "https://example.com/demo.git", "include_extensions": ["md"]},
        )
        changed = ingest_source(
            source_type="git",
            source_name="项目仓库",
            config={"url": "https://example.com/demo.git", "include_extensions": ["md"]},
        )

    assert hashlib.sha256(first.body).hexdigest() != hashlib.sha256(changed.body).hexdigest()


def test_mcp_adapter_adds_streamable_http_accept_header() -> None:
    ingestor = MagicMock()
    ingestor.ingest_all_resources.return_value = []
    with (
        patch("semantica.ingest.mcp_ingestor.MCPIngestor", return_value=ingestor),
        patch("packages.semantica_adapter.ingest._assert_network_target"),
    ):
        ingest_source(
            source_type="mcp",
            source_name="MCP",
            config={"url": "https://example.com/mcp", "server_name": "fixture"},
        )

    _, kwargs = ingestor.connect.call_args
    assert kwargs["headers"]["Accept"] == "application/json, text/event-stream"
