#!/usr/bin/env python3
"""Live FTP/SFTP/POP3S/MCP/MySQL source checks on the Docker network."""

from __future__ import annotations

import json
import io
import zipfile
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.semantica_adapter.ingest import ingest_source


PASSWORD = "Fixture@123456"


def check(name: str, source_type: str, config: dict[str, Any], secret: str | None = None) -> dict[str, Any]:
    result = ingest_source(source_type=source_type, source_name=name, config=config, secret=secret)
    assert result.body, name
    searchable = result.body
    if result.content_type == "application/zip":
        with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
            searchable = b"\n".join(archive.read(member) for member in archive.namelist())
    assert b"NexusOne" in searchable, name
    return {"bytes": len(result.body), **result.metadata}


def main() -> None:
    results = {
        "ftp": check(
            "ftp-live",
            "ftp",
            {"host": "protocol-fixture", "port": 2121, "username": "fixture", "path": "/", "recursive": True},
            PASSWORD,
        ),
        "ftps": check(
            "ftps-live",
            "ftps",
            {"host": "protocol-fixture", "port": 2990, "username": "fixture", "path": "/", "recursive": True},
            PASSWORD,
        ),
        "sftp": check(
            "sftp-live",
            "sftp",
            {"host": "protocol-fixture", "port": 2222, "username": "fixture", "path": "/", "recursive": True},
            PASSWORD,
        ),
        "email_pop3s": check(
            "email-live",
            "email",
            {"server": "protocol-fixture", "port": 1995, "username": "fixture", "protocol": "pop3", "max_emails": 5},
            PASSWORD,
        ),
        "email_imaps": check(
            "email-imap-live",
            "email",
            {"server": "protocol-fixture", "port": 1993, "username": "fixture", "protocol": "imap", "mailbox": "INBOX", "max_emails": 5},
            PASSWORD,
        ),
        "mcp": check(
            "mcp-live",
            "mcp",
            {"url": "http://protocol-fixture:8095/mcp", "server_name": "fixture", "timeout": 30},
        ),
        "mysql": check(
            "mysql-live",
            "database",
            {
                "dialect": "mysql",
                "host": "mysql-fixture",
                "port": 3306,
                "database": "knowledge_fixture",
                "username": "fixture",
                "include_tables": ["product_facts"],
                "max_rows_per_table": 10,
            },
            PASSWORD,
        ),
        "mongodb": check(
            "mongodb-live",
            "mongodb",
            {"host": "mongo-fixture", "port": 27017, "database": "knowledge_fixture", "collection": "product_facts", "limit": 10},
        ),
        "smb": check(
            "smb-live",
            "smb",
            {"server": "smb-fixture", "port": 445, "share": "knowledge", "path": "", "username": "fixture", "recursive": True, "max_files": 100},
            PASSWORD,
        ),
        "git": check(
            "git-live",
            "git",
            {"url": "git://git-fixture/repo.git", "depth": 1, "include_extensions": ["md", "txt"]},
        ),
    }
    assert results["ftp"]["file_count"] == 2
    assert results["ftps"]["file_count"] == 2
    assert results["sftp"]["file_count"] == 2
    assert results["email_pop3s"]["email_count"] == 1
    assert results["email_imaps"]["email_count"] == 1
    assert results["mcp"]["resource_count"] == 1
    assert results["mysql"]["table_count"] == 1
    assert results["mongodb"]["document_count"] == 1
    assert results["smb"]["file_count"] >= 1
    assert results["git"]["file_count"] >= 1
    print(json.dumps({"connectors": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
