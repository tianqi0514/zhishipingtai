from __future__ import annotations

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
