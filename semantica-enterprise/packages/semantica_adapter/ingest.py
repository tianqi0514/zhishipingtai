from __future__ import annotations

import json
import io
import hashlib
import mimetypes
import os
import re
import socket
import shutil
import subprocess
import tempfile
import threading
import zipfile
from email import policy as email_policy
from email.parser import BytesParser
from dataclasses import dataclass, field
from datetime import date, datetime
from ftplib import FTP, FTP_TLS
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urljoin
from xml.etree import ElementTree

import httpx

from packages.platform.config import get_settings


@dataclass(frozen=True)
class IngestedPayload:
    body: bytes
    filename: str
    content_type: str
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)


MAX_SOURCE_FILES = 5_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
_REPO_PRIVATE_VALIDATION_LOCK = threading.Lock()


def _safe_relative_name(value: str) -> str:
    parts = []
    for part in PurePosixPath(str(value).replace("\\", "/")).parts:
        if part in {"", ".", "..", "/"}:
            continue
        clean = re.sub(r"[^\w.()\[\] -]", "_", part, flags=re.UNICODE).strip(" .")
        if clean:
            parts.append(clean[:180])
    return "/".join(parts) or "item.bin"


def _archive_payload(
    source_name: str,
    files: list[tuple[str, bytes]],
    metadata: dict[str, Any] | None = None,
) -> IngestedPayload:
    if len(files) > MAX_SOURCE_FILES:
        raise ValueError(f"数据源文件数量超过上限 {MAX_SOURCE_FILES}")
    total = sum(len(body) for _, body in files)
    if total > MAX_SOURCE_BYTES:
        raise ValueError("数据源同步内容超过 2 GiB 上限")
    if not files:
        raise ValueError("数据源中没有可同步文件")
    output = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        # Stable ordering and timestamps are required for source-level SHA-256
        # deduplication. The default ZipInfo timestamp is the current clock,
        # which would create a false new document version on every sync.
        for index, (name, body) in enumerate(sorted(files, key=lambda item: item[0]), start=1):
            safe_name = _safe_relative_name(name)
            if safe_name in seen:
                path = PurePosixPath(safe_name)
                safe_name = str(path.with_name(f"{path.stem}-{index}{path.suffix}"))
            seen.add(safe_name)
            member = zipfile.ZipInfo(safe_name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, body, compresslevel=6)
    return IngestedPayload(
        body=output.getvalue(),
        filename=_safe_source_filename(source_name, ".zip"),
        content_type="application/zip",
        title=source_name,
        metadata={"file_count": len(files), "content_bytes": total, **(metadata or {})},
    )


def extract_media_payloads(
    payload: IngestedPayload,
    *,
    maximum_items: int = 100,
    maximum_total_bytes: int = MAX_SOURCE_BYTES,
    maximum_compression_ratio: float = 200.0,
) -> list[IngestedPayload]:
    """Safely expose media members so each receives the full media pipeline.

    Multi-file connectors intentionally keep their deterministic ZIP snapshot
    for source-level incremental history. Media members are additionally
    materialized as child documents; this avoids treating a video merely as an
    opaque archive attachment and preserves the existing archive contract.
    """
    from packages.platform.media import media_type_for

    discovered: list[tuple[str, bytes, str]] = []
    if payload.content_type == "application/zip" or Path(payload.filename).suffix.casefold() == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(payload.body)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if len(members) > MAX_SOURCE_FILES:
                    raise ValueError("数据源归档文件数量超过安全上限")
                total = 0
                for member in members:
                    safe_name = _safe_relative_name(member.filename)
                    if member.flag_bits & 0x1:
                        raise ValueError("数据源归档包含加密文件，无法安全解析")
                    if member.compress_size and member.file_size / member.compress_size > maximum_compression_ratio:
                        raise ValueError("数据源归档压缩比超过安全上限")
                    total += int(member.file_size)
                    if total > maximum_total_bytes:
                        raise ValueError("数据源归档解压大小超过安全上限")
                    content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
                    if media_type_for(safe_name, content_type):
                        discovered.append((safe_name, archive.read(member), content_type))
        except zipfile.BadZipFile as exc:
            raise ValueError("数据源归档已损坏") from exc
    elif payload.content_type == "message/rfc822" or Path(payload.filename).suffix.casefold() == ".eml":
        message = BytesParser(policy=email_policy.default).parsebytes(payload.body)
        for part in message.iter_attachments():
            name = _safe_relative_name(part.get_filename() or "attachment.bin")
            content_type = str(part.get_content_type() or mimetypes.guess_type(name)[0] or "application/octet-stream")
            if media_type_for(name, content_type):
                discovered.append((name, part.get_payload(decode=True) or b"", content_type))
    if len(discovered) > maximum_items:
        raise ValueError(f"本次同步包含 {len(discovered)} 个媒体文件，超过配置上限 {maximum_items}")
    return [
        IngestedPayload(
            body=body,
            filename=f"{hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]}-{Path(name).name}",
            content_type=content_type,
            title=f"{payload.title} · {name}",
            metadata={"parent_snapshot": payload.filename, "source_path": name, "media_attachment": True},
        )
        for name, body, content_type in discovered
        if body
    ]


def _source_roots() -> list[Path]:
    return [Path(value).resolve() for value in get_settings().source_mount_roots.split(os.pathsep) if value.strip()]


def _safe_local_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not any(path == root or path.is_relative_to(root) for root in _source_roots()):
        raise ValueError("本地数据源路径不在允许挂载目录中")
    if not path.exists():
        raise ValueError("本地数据源路径不存在")
    return path


def _private_host_allowed(host: str) -> bool:
    allowed = {
        item.strip().casefold()
        for item in get_settings().source_private_host_allowlist.split(",")
        if item.strip()
    }
    return host.casefold() in allowed


def _assert_network_target(host: str) -> None:
    if _private_host_allowed(host):
        return
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError(f"无法解析数据源主机：{host}") from exc
    for address in addresses:
        parsed = ip_address(address)
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_reserved:
            raise ValueError("数据源目标为私有或保留地址，且不在服务端允许名单中")


def _http_client(url: str, *, secret: str | None = None, username: str | None = None, timeout: int = 30):
    parsed = httpx.URL(url)
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError("数据源 URL 必须是 HTTP 或 HTTPS 地址")
    _assert_network_target(parsed.host)
    headers = {"Authorization": f"Bearer {secret}"} if secret and not username else {}
    auth = (username, secret or "") if username else None
    return httpx.Client(timeout=timeout, follow_redirects=False, headers=headers, auth=auth)


def _oauth_access_token(secret: str | None, config: dict[str, Any]) -> str:
    if not secret:
        raise ValueError("该数据源尚未配置 OAuth 凭据")
    try:
        credential = json.loads(secret)
    except json.JSONDecodeError:
        return secret
    if not isinstance(credential, dict):
        raise ValueError("OAuth 凭据必须是 JSON 对象或访问令牌")
    if credential.get("refresh_token"):
        token_url = str(
            credential.get("token_uri")
            or config.get("token_url")
            or "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        )
        parsed = httpx.URL(token_url)
        _assert_network_target(parsed.host or "")
        response = httpx.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": credential["refresh_token"],
                "client_id": credential.get("client_id") or config.get("client_id"),
                "client_secret": credential.get("client_secret") or config.get("client_secret"),
                "scope": credential.get("scope") or config.get("scope") or "https://graph.microsoft.com/.default offline_access",
            },
            timeout=int(config.get("timeout") or 30),
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if token:
            return str(token)
    token = credential.get("access_token") or credential.get("token")
    if not token:
        raise ValueError("OAuth 凭据中缺少 access_token 或 refresh_token")
    return str(token)


def _safe_source_filename(source_name: str, suffix: str) -> str:
    safe_name = "".join(
        character if character.isalnum() or character in {"-", "_", " ", "."} else "_"
        for character in source_name
    ).strip(" .")
    return f"{(safe_name or 'source')[:180]}{suffix}"


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def _json_payload(source_name: str, data: Any, metadata: dict[str, Any]) -> IngestedPayload:
    return IngestedPayload(
        body=json.dumps(data, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8"),
        filename=_safe_source_filename(source_name, ".json"),
        content_type="application/json",
        title=source_name,
        metadata=metadata,
    )


def _ingest_google_drive_http(
    source_name: str,
    config: dict[str, Any],
    secret: str,
) -> IngestedPayload:
    """Official Drive v3 protocol adapter for proxies and protocol tests.

    Normal Google connections continue to use Semantica's GDriveIngestor. This
    narrow adapter keeps custom enterprise gateways testable without coupling
    the platform to Google's discovery document loader.
    """
    oauth_config = {
        **config,
        "token_url": config.get("token_url") or "https://oauth2.googleapis.com/token",
        "scope": config.get("scope") or "https://www.googleapis.com/auth/drive.readonly",
    }
    token = _oauth_access_token(secret, oauth_config)
    api_base = str(config.get("api_base_url") or "https://www.googleapis.com/drive/v3").rstrip("/")
    _assert_network_target(httpx.URL(api_base).host or "")
    max_files = int(config.get("max_files") or MAX_SOURCE_FILES)
    recursive = bool(config.get("recursive", True))
    folders = [str(config.get("folder_id") or "root")]
    files: list[tuple[str, bytes]] = []
    google_exports = {
        "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
        "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
        "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
    }
    with _http_client(api_base, secret=token, timeout=int(config.get("timeout") or 30)) as client:
        while folders:
            folder_id = folders.pop(0)
            page_token = ""
            while True:
                response = client.get(
                    f"{api_base}/files",
                    params={
                        "q": f"'{folder_id}' in parents and trashed = false",
                        "pageSize": min(max_files, 1000),
                        "pageToken": page_token or None,
                        "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime,size,md5Checksum)",
                        "supportsAllDrives": "true",
                        "includeItemsFromAllDrives": "true",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("files") or []:
                    mime = str(item.get("mimeType") or "")
                    if mime == "application/vnd.google-apps.folder":
                        if recursive:
                            folders.append(str(item["id"]))
                        continue
                    name = _safe_relative_name(str(item.get("name") or item["id"]))
                    if mime in google_exports:
                        export_mime, extension = google_exports[mime]
                        downloaded = client.get(
                            f"{api_base}/files/{quote(str(item['id']), safe='')}/export",
                            params={"mimeType": export_mime},
                        )
                        if not Path(name).suffix:
                            name += extension
                    else:
                        downloaded = client.get(
                            f"{api_base}/files/{quote(str(item['id']), safe='')}",
                            params={"alt": "media", "supportsAllDrives": "true"},
                        )
                    downloaded.raise_for_status()
                    files.append((name, downloaded.content))
                    if len(files) > max_files:
                        raise ValueError("Google Drive 文件数量超过配置上限")
                page_token = str(payload.get("nextPageToken") or "")
                if not page_token:
                    break
    return _archive_payload(
        source_name,
        files,
        {"folder_id": str(config.get("folder_id") or "root"), "remote_file_count": len(files)},
    )


def ingest_source(
    *,
    source_type: str,
    source_name: str,
    config: dict[str, Any],
    secret: str | None = None,
) -> IngestedPayload:
    """Run a Semantica source ingestor and normalize its result for the platform."""
    url = str(config.get("url") or "").strip()

    if source_type == "web":
        from semantica.ingest.web_ingestor import WebIngestor

        host = httpx.URL(url).host or ""
        _assert_network_target(host)
        content = WebIngestor(
            delay=float(config.get("delay", 0.2)),
            respect_robots=bool(config.get("respect_robots", True)),
            max_retries=int(config.get("max_retries", 3)),
            timeout=int(config.get("timeout", 30)),
            allow_private_ips=_private_host_allowed(host),
        ).ingest_url(url)
        body = content.html or f"<html><body><pre>{content.text}</pre></body></html>"
        return IngestedPayload(
            body=body.encode("utf-8"),
            filename=_safe_source_filename(source_name, ".html"),
            content_type="text/html",
            title=content.title or source_name,
            metadata={
                "status_code": content.status_code,
                "text_length": len(content.text or ""),
                "link_count": len(content.links or []),
            },
        )

    if source_type == "rest":
        if str(config.get("response_mode") or "json") == "binary":
            host = httpx.URL(url).host or ""
            _assert_network_target(host)
            headers = dict(config.get("headers") or {})
            if secret:
                header_name = str(config.get("secret_header") or "Authorization")
                headers[header_name] = str(config.get("secret_prefix") or "Bearer ") + secret
            with _http_client(url, timeout=int(config.get("timeout", 30))) as client:
                response = client.request(
                    str(config.get("method") or "GET"), url, headers=headers,
                    params=config.get("params"), json=config.get("body"),
                )
                response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "application/octet-stream").split(";", 1)[0]
            suffix = mimetypes.guess_extension(content_type) or Path(httpx.URL(url).path).suffix or ".bin"
            return IngestedPayload(
                body=response.content,
                filename=_safe_source_filename(source_name, suffix),
                content_type=content_type,
                title=source_name,
                metadata={"status_code": response.status_code, "response_mode": "binary"},
            )
        from semantica.ingest.api_ingestor import RESTIngestor

        host = httpx.URL(url).host or ""
        _assert_network_target(host)
        headers = dict(config.get("headers") or {})
        if secret:
            header_name = str(config.get("secret_header") or "Authorization")
            prefix = str(config.get("secret_prefix") or "Bearer ")
            headers[header_name] = prefix + secret
        content = RESTIngestor(
            config={
                "allow_private_ips": _private_host_allowed(host),
                "timeout": int(config.get("timeout", 30)),
                "max_retries": int(config.get("max_retries", 3)),
            }
        ).ingest_endpoint(
            url,
            method=str(config.get("method") or "GET"),
            headers=headers,
            params=config.get("params"),
            json_data=config.get("body"),
        )
        return _json_payload(source_name, content.data, {"status_code": content.response_status})

    if source_type == "rss":
        from semantica.ingest.feed_ingestor import FeedIngestor

        host = httpx.URL(url).host or ""
        _assert_network_target(host)
        feed = FeedIngestor(allow_private_ips=_private_host_allowed(host)).ingest_feed(
            url,
            timeout=int(config.get("timeout", 30)),
            validate=bool(config.get("validate", True)),
        )
        max_items = int(config.get("max_items", 100))
        items = feed.items[:max_items]
        payload = {
            "title": feed.title,
            "link": feed.link,
            "description": feed.description,
            "language": feed.language,
            "updated": feed.updated,
            "items": items,
        }
        result = _json_payload(source_name, payload, {"item_count": len(items)})
        return IngestedPayload(**{**vars(result), "title": feed.title or source_name})

    if source_type == "sitemap":
        from semantica.ingest.web_ingestor import WebIngestor

        host = httpx.URL(url).host or ""
        _assert_network_target(host)
        crawl_options = {
            "max_urls": int(config.get("max_urls", 50)),
            "fail_fast": bool(config.get("fail_fast", False)),
        }
        if config.get("pattern"):
            crawl_options["pattern"] = str(config["pattern"])
        if config.get("exclude_pattern"):
            crawl_options["exclude_pattern"] = str(config["exclude_pattern"])
        pages = WebIngestor(
            delay=float(config.get("delay", 0.2)),
            respect_robots=bool(config.get("respect_robots", True)),
            max_retries=int(config.get("max_retries", 3)),
            timeout=int(config.get("timeout", 30)),
            allow_private_ips=_private_host_allowed(host),
        ).crawl_sitemap(url, **crawl_options)
        payload = [
            {"url": page.url, "title": page.title, "text": page.text, "fetched_at": page.fetched_at}
            for page in pages
        ]
        return _json_payload(source_name, payload, {"page_count": len(pages)})

    if source_type == "git":
        from semantica.ingest.repo_ingestor import RepoIngestor

        _assert_network_target(httpx.URL(url).host or "")

        options = {
            "depth": int(config.get("depth", 1)),
            "single_branch": True,
            "no_tags": True,
            "include_history": bool(config.get("include_history", False)),
            "include_extensions": config.get("include_extensions") or ["md", "txt", "py", "js", "ts", "java", "go", "rs", "yaml", "yml", "json"],
        }
        if config.get("branch"):
            options["branch"] = str(config["branch"])
        ingestor = RepoIngestor()
        if _private_host_allowed(httpx.URL(url).host or ""):
            # The pinned Semantica repository ingestor has no explicit private
            # host allowlist hook. The platform has already resolved and
            # allowlisted the target, so isolate this version-specific bridge
            # under a lock and restore the original validator immediately.
            with _REPO_PRIVATE_VALIDATION_LOCK:
                original_validator = RepoIngestor._validate_repo_host
                RepoIngestor._validate_repo_host = staticmethod(lambda _host: None)
                try:
                    result = ingestor.ingest_repository(url, **options)
                finally:
                    RepoIngestor._validate_repo_host = staticmethod(original_validator)
        else:
            result = ingestor.ingest_repository(url, **options)
        repo_path = Path(str(result.pop("temp_path", "")))
        media_files: list[tuple[str, bytes]] = []
        if repo_path.is_dir():
            attributes = repo_path / ".gitattributes"
            uses_lfs = attributes.is_file() and "filter=lfs" in attributes.read_text(
                encoding="utf-8", errors="ignore"
            )
            if uses_lfs:
                if not shutil.which("git-lfs"):
                    raise ValueError("仓库使用 Git LFS，但运行环境未安装 git-lfs")
                pulled = subprocess.run(
                    ["git", "lfs", "pull"], cwd=repo_path, capture_output=True,
                    text=True, timeout=int(config.get("lfs_timeout") or 600), check=False,
                )
                if pulled.returncode:
                    raise ValueError(f"Git LFS 文件拉取失败：{(pulled.stderr or '未知错误')[-500:]}")
            for candidate in sorted(repo_path.rglob("*")):
                if not candidate.is_file() or ".git" in candidate.relative_to(repo_path).parts:
                    continue
                relative = candidate.relative_to(repo_path).as_posix()
                content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
                from packages.platform.media import media_type_for
                if media_type_for(relative, content_type):
                    media_files.append((relative, candidate.read_bytes()))
        metadata = {
            "file_count": len(result.get("code_files") or []),
            "commit_count": len(result.get("commits") or []),
            "media_file_count": len(media_files),
        }
        if media_files:
            manifest = json.dumps(result, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
            return _archive_payload(source_name, [("repository.json", manifest), *media_files], metadata)
        return _json_payload(source_name, result, metadata)

    if source_type == "database":
        from semantica.ingest.db_ingestor import DBIngestor

        dialect = str(config.get("dialect") or "postgresql")
        driver = "postgresql+psycopg" if dialect == "postgresql" else "mysql+pymysql"
        default_port = 5432 if dialect == "postgresql" else 3306
        username = quote_plus(str(config.get("username") or ""))
        password = quote_plus(secret or "")
        host = str(config.get("host") or "")
        _assert_network_target(host)
        port = int(config.get("port") or default_port)
        database = quote_plus(str(config.get("database") or ""))
        connection_string = f"{driver}://{username}:{password}@{host}:{port}/{database}"
        include_tables = config.get("include_tables") or None
        exclude_tables = config.get("exclude_tables") or None
        max_rows = int(config.get("max_rows_per_table", 1000))
        if dialect == "mysql":
            # Semantica 0.6.6 quotes every table with ANSI double quotes; a
            # normal MySQL/MariaDB server rejects that unless ANSI_QUOTES is
            # enabled. Reuse its schema exporter and keep the dialect-specific
            # row reader as a narrow adapter until the pinned release fixes it.
            from sqlalchemy import MetaData, Table, create_engine, inspect, select

            ingestor = DBIngestor()
            engine = create_engine(connection_string, pool_pre_ping=True)
            try:
                inspector = inspect(engine)
                available = inspector.get_table_names()
                selected = [name for name in available if not include_tables or name in include_tables]
                selected = [name for name in selected if not exclude_tables or name not in exclude_tables]
                schema = ingestor.exporter.export_schema(engine)
                tables: dict[str, Any] = {}
                metadata = MetaData()
                with engine.connect() as connection:
                    for table_name in selected:
                        table = Table(table_name, metadata, autoload_with=engine)
                        rows = [dict(row) for row in connection.execute(select(table).limit(max_rows)).mappings()]
                        tables[table_name] = {
                            "table_name": table_name,
                            "columns": [
                                {"name": column.name, "type": str(column.type), "nullable": column.nullable}
                                for column in table.columns
                            ],
                            "rows": rows,
                            "row_count": len(rows),
                            "schema": None,
                        }
                result = {
                    "schema": schema,
                    "tables": tables,
                    "total_tables": len(tables),
                }
            finally:
                engine.dispose()
        else:
            result = DBIngestor().ingest_database(
                connection_string,
                include_tables=include_tables,
                exclude_tables=exclude_tables,
                max_rows_per_table=max_rows,
            )
        result["connection_string"] = f"{dialect}://{host}:{port}/{config.get('database')}"
        return _json_payload(source_name, result, {"table_count": result.get("total_tables", 0)})

    if source_type == "email":
        from semantica.ingest.email_ingestor import EmailIngestor

        protocol = str(config.get("protocol") or "imap").lower()
        _assert_network_target(str(config.get("server") or ""))
        ingestor = EmailIngestor()
        if protocol == "imap":
            ingestor.connect_imap(
                str(config.get("server")),
                int(config.get("port") or 993),
                str(config.get("username")),
                secret,
            )
        else:
            ingestor.connect_pop3(
                str(config.get("server")),
                int(config.get("port") or 995),
                str(config.get("username")),
                secret,
            )
        try:
            emails = ingestor.ingest_mailbox(
                mailbox_name=str(config.get("mailbox") or "INBOX"),
                protocol=protocol,
                since=config.get("since") or None,
                max_emails=int(config.get("max_emails", 100)),
                unread_only=bool(config.get("unread_only", False)),
            )
            files: list[tuple[str, bytes]] = []
            serialized: list[dict[str, Any]] = []
            for email_index, message in enumerate(emails):
                value = dict(vars(message)) if hasattr(message, "__dict__") else dict(message)
                clean_attachments = []
                for attachment_index, attachment in enumerate(value.get("attachments") or []):
                    info = dict(attachment)
                    saved_path = info.pop("saved_path", None)
                    clean_attachments.append(info)
                    if saved_path and Path(str(saved_path)).is_file():
                        attachment_name = _safe_relative_name(
                            f"attachments/{email_index:04d}/{attachment_index:04d}-{info.get('filename') or 'attachment.bin'}"
                        )
                        files.append((attachment_name, Path(str(saved_path)).read_bytes()))
                value["attachments"] = clean_attachments
                serialized.append(value)
            files.append((
                "emails.json",
                json.dumps(serialized, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8"),
            ))
            return _archive_payload(
                source_name,
                files,
                {"email_count": len(emails), "attachment_count": max(0, len(files) - 1)},
            )
        finally:
            try:
                ingestor.disconnect()
            finally:
                ingestor.attachment_processor.cleanup_attachments()

    if source_type == "mcp":
        from semantica.ingest.mcp_ingestor import MCPIngestor

        _assert_network_target(httpx.URL(url).host or "")

        # Semantica's locked MCP client predates the mandatory dual Accept
        # header used by modern streamable-HTTP servers. Keep this protocol
        # compatibility shim in the out-of-tree platform adapter.
        headers = {
            "Accept": "application/json, text/event-stream",
            **dict(config.get("headers") or {}),
        }
        if secret:
            headers[str(config.get("secret_header") or "Authorization")] = (
                str(config.get("secret_prefix") or "Bearer ") + secret
            )
        ingestor = MCPIngestor()
        server_name = str(config.get("server_name") or source_name)
        ingestor.connect(server_name, url=url, headers=headers, timeout=int(config.get("timeout", 30)))
        resources = ingestor.ingest_all_resources(server_name)
        return _json_payload(source_name, resources, {"resource_count": len(resources)})

    if source_type == "mongodb":
        from semantica.ingest.mongo_ingestor import MongoIngestor

        host = str(config.get("host") or "").strip()
        _assert_network_target(host)
        username = quote_plus(str(config.get("username") or ""))
        password = quote_plus(secret or "")
        credentials = f"{username}:{password}@" if username else ""
        port = int(config.get("port") or 27017)
        auth_source = quote_plus(str(config.get("auth_database") or "admin"))
        connection = f"mongodb://{credentials}{host}:{port}/?authSource={auth_source}"
        result = MongoIngestor().ingest_collection(
            connection,
            str(config.get("database") or ""),
            str(config.get("collection") or ""),
            limit=int(config.get("limit") or 1000),
            query=dict(config.get("query") or {}),
        )
        return _json_payload(
            source_name,
            result,
            {"document_count": result.document_count, "collection": result.collection_name},
        )

    if source_type == "opensearch":
        url = str(config.get("url") or "").strip().rstrip("/")
        parsed = httpx.URL(url)
        _assert_network_target(parsed.host or "")
        index = str(config.get("index") or "").strip()
        limit = int(config.get("limit") or 1000)
        headers = dict(config.get("headers") or {})
        username = str(config.get("username") or "") or None
        if secret and not username:
            headers[str(config.get("secret_header") or "Authorization")] = (
                str(config.get("secret_prefix") or "Bearer ") + secret
            )
        with _http_client(
            url,
            secret=secret if username else None,
            username=username,
            timeout=int(config.get("timeout") or 30),
        ) as client:
            response = client.post(
                f"{url}/{quote_plus(index)}/_search",
                headers=headers,
                json={
                    "size": limit,
                    "query": dict(config.get("query") or {"match_all": {}}),
                    "track_total_hits": True,
                },
            )
            response.raise_for_status()
            payload = response.json()
        hits = (payload.get("hits") or {}).get("hits") or []
        records = [
            {
                "_id": hit.get("_id"),
                "_index": hit.get("_index"),
                "_score": hit.get("_score"),
                "source": hit.get("_source") or {},
            }
            for hit in hits
        ]
        total = (payload.get("hits") or {}).get("total") or 0
        if isinstance(total, dict):
            total = total.get("value") or 0
        return _json_payload(
            source_name,
            records,
            {"document_count": len(records), "total_hits": int(total), "index": index},
        )

    if source_type == "elasticsearch":
        from semantica.ingest.elastic_ingestor import ElasticIngestor

        url = str(config.get("url") or "").strip()
        parsed = httpx.URL(url)
        _assert_network_target(parsed.host or "")
        client_config: dict[str, Any] = {
            "request_timeout": int(config.get("timeout") or 30),
            "verify_certs": bool(config.get("verify_certs", True)),
        }
        username = str(config.get("username") or "")
        if username:
            client_config["basic_auth"] = (username, secret or "")
        elif secret:
            client_config["api_key"] = secret
        result = ElasticIngestor(config={"client_config": client_config}).ingest_index(
            url,
            str(config.get("index") or ""),
            limit=int(config.get("limit") or 1000),
            query=dict(config.get("query") or {"match_all": {}}),
        )
        return _json_payload(
            source_name,
            result,
            {"document_count": result.document_count, "index": result.index_name},
        )

    if source_type in {"duckdb", "parquet", "arrow"}:
        path = _safe_local_path(str(config.get("path") or ""))
        columns = config.get("columns") or None
        limit = int(config.get("limit") or 1000)
        if source_type == "parquet":
            from semantica.ingest.parquet_ingestor import ParquetIngestor

            result = ParquetIngestor().ingest(path, columns=columns, limit=limit)
        elif source_type == "arrow":
            from semantica.ingest.arrow_ingestor import ArrowIngestor

            result = ArrowIngestor().ingest(path, columns=columns, limit=limit)
        else:
            from semantica.ingest.duckdb_ingestor import DuckDBIngestor

            suffix = path.suffix.casefold()
            ingestor = DuckDBIngestor(config={"memory_limit": str(config.get("memory_limit") or "1GB")})
            if suffix == ".csv":
                result = ingestor.ingest_csv(path, limit=limit)
            elif suffix in {".parquet", ".pq"}:
                result = ingestor.ingest_parquet(path, limit=limit)
            elif suffix in {".xlsx", ".xls"}:
                result = ingestor.ingest_excel(path, limit=limit)
            else:
                raise ValueError("DuckDB 数据源仅接受 CSV、Parquet 或 Excel 文件")
        return _json_payload(source_name, result, {"path": str(path), "row_count": getattr(result, "row_count", 0)})

    if source_type == "huggingface":
        from semantica.ingest.huggingface_ingestor import HuggingFaceIngestor

        options: dict[str, Any] = {}
        if secret:
            options["token"] = secret
        if config.get("revision"):
            options["revision"] = str(config["revision"])
        if config.get("subset"):
            options["name"] = str(config["subset"])
        if config.get("data_files"):
            configured_files = config["data_files"]
            if isinstance(configured_files, dict):
                options["data_files"] = {
                    str(split): str(_safe_local_path(str(value)))
                    for split, value in configured_files.items()
                }
            else:
                values = configured_files if isinstance(configured_files, list) else [configured_files]
                options["data_files"] = [str(_safe_local_path(str(value))) for value in values]
        result = HuggingFaceIngestor().ingest_dataset(
            str(config.get("dataset") or ""),
            split=str(config.get("split") or "train"),
            limit=int(config.get("limit") or 1000),
            **options,
        )
        return _json_payload(source_name, result, {"row_count": result.row_count, "dataset": result.dataset_name})

    if source_type == "snowflake":
        from semantica.ingest.snowflake_ingestor import SnowflakeIngestor

        ingestor = SnowflakeIngestor(
            account=str(config.get("account") or ""),
            user=str(config.get("username") or ""),
            password=secret,
            warehouse=str(config.get("warehouse") or ""),
            database=str(config.get("database") or ""),
            schema=str(config.get("schema") or "PUBLIC"),
            role=str(config.get("role") or "") or None,
        )
        result = ingestor.ingest_table(
            str(config.get("table") or ""),
            limit=int(config.get("limit") or 1000),
        )
        return _json_payload(source_name, result, {"row_count": result.row_count, "table": result.table_name})

    if source_type == "databricks":
        from semantica.ingest.databricks_ingestor import DatabricksIngestor

        host = str(config.get("host") or "")
        parsed_host = httpx.URL(host if "://" in host else f"https://{host}").host or ""
        _assert_network_target(parsed_host)
        result = DatabricksIngestor(
            host=host,
            token=secret,
            http_path=str(config.get("http_path") or ""),
            catalog=str(config.get("catalog") or "main"),
            schema=str(config.get("schema") or "default"),
        ).ingest_table(str(config.get("table") or ""), limit=int(config.get("limit") or 1000))
        return _json_payload(source_name, result, {"row_count": result.row_count, "table": result.table_name})

    if source_type == "stream":
        stream_type = str(config.get("stream_type") or "rabbitmq")
        if stream_type != "rabbitmq":
            raise ValueError("当前同步式数据源仅支持 RabbitMQ；Kafka/Pulsar/Kinesis 请使用事件接入服务")
        from semantica.ingest.stream_ingestor import StreamIngestor

        host = str(config.get("host") or "").strip()
        _assert_network_target(host)
        username = quote_plus(str(config.get("username") or "guest"))
        password = quote_plus(secret or "guest")
        vhost = quote_plus(str(config.get("vhost") or "/"), safe="")
        connection_url = f"amqp://{username}:{password}@{host}:{int(config.get('port') or 5672)}/{vhost}"
        processor = StreamIngestor().ingest_rabbitmq(
            str(config.get("queue") or ""),
            connection_url,
            durable=bool(config.get("durable", True)),
        )
        messages: list[Any] = []
        try:
            for _ in range(int(config.get("max_messages") or 100)):
                method, _properties, body = processor.channel.basic_get(processor.queue, auto_ack=False)
                if method is None:
                    break
                messages.append(processor.process_message(body))
                processor.channel.basic_ack(method.delivery_tag)
        finally:
            processor.channel.close()
            processor.connection.close()
        return _json_payload(source_name, messages, {"message_count": len(messages), "stream_type": stream_type})

    if source_type == "google_drive":
        from semantica.ingest.gdrive_ingestor import GDriveIngestor

        if not secret:
            raise ValueError("Google Drive 必须配置 OAuth Token JSON")
        if config.get("api_base_url"):
            return _ingest_google_drive_http(source_name, config, secret)
        try:
            token_data = json.loads(secret)
        except json.JSONDecodeError as exc:
            raise ValueError("Google Drive 密钥必须是 OAuth Token JSON") from exc
        token_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(token_data, token_file, ensure_ascii=False)
            token_file.close()
            ingestor = GDriveIngestor(token_path=token_file.name)
            folder = ingestor.ingest_folder(
                str(config.get("folder_id") or "root"),
                include_subfolders=bool(config.get("recursive", True)),
                file_types=config.get("mime_types") or None,
            )
            files: list[tuple[str, bytes]] = []
            for item in folder.files[: int(config.get("max_files") or MAX_SOURCE_FILES)]:
                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    continue
                downloaded = ingestor.ingest_file(str(item["id"]), download=True, export_mime_type="application/pdf")
                name = str(item.get("name") or item["id"])
                if str(item.get("mimeType") or "").startswith("application/vnd.google-apps") and not Path(name).suffix:
                    name += ".pdf"
                files.append((name, bytes(downloaded.get("content") or b"")))
            return _archive_payload(
                source_name,
                files,
                {"folder_id": folder.folder_id, "remote_file_count": folder.file_count},
            )
        finally:
            try:
                os.unlink(token_file.name)
            except FileNotFoundError:
                pass

    if source_type in {"onedrive", "sharepoint"}:
        token = _oauth_access_token(secret, config)
        graph_base = str(config.get("graph_base_url") or "https://graph.microsoft.com/v1.0").rstrip("/")
        parsed = httpx.URL(graph_base)
        _assert_network_target(parsed.host or "")
        drive_id = str(config.get("drive_id") or "").strip()
        if source_type == "sharepoint" and not drive_id:
            site_id = str(config.get("site_id") or "").strip()
            if not site_id:
                raise ValueError("SharePoint 必须配置 site_id 或 drive_id")
            drive_endpoint = f"{graph_base}/sites/{quote_plus(site_id)}/drive"
            with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=int(config.get("timeout") or 30)) as client:
                drive_response = client.get(drive_endpoint)
                drive_response.raise_for_status()
                drive_id = str(drive_response.json().get("id") or "")
        if not drive_id:
            drive_id = "me"
        remote_path = str(config.get("path") or "").strip("/")
        if drive_id == "me":
            children_url = f"{graph_base}/me/drive/root"
        else:
            children_url = f"{graph_base}/drives/{quote_plus(drive_id)}/root"
        if remote_path:
            children_url += f":/{remote_path}:"
        children_url += "/children"
        files: list[tuple[str, bytes]] = []
        queue: list[tuple[str, str]] = [(children_url, "")]
        with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=int(config.get("timeout") or 30)) as client:
            while queue:
                listing_url, parent = queue.pop(0)
                while listing_url:
                    response = client.get(listing_url)
                    response.raise_for_status()
                    payload = response.json()
                    for item in payload.get("value") or []:
                        name = _safe_relative_name(f"{parent}/{item.get('name') or item.get('id')}")
                        if item.get("folder") and bool(config.get("recursive", True)):
                            queue.append((f"{graph_base}/drives/{quote_plus(str(item.get('parentReference', {}).get('driveId') or drive_id))}/items/{quote_plus(str(item['id']))}/children", name))
                            continue
                        if not item.get("file"):
                            continue
                        download_url = item.get("@microsoft.graph.downloadUrl")
                        if download_url:
                            download_host = httpx.URL(download_url).host or ""
                            _assert_network_target(download_host)
                            downloaded = httpx.get(download_url, timeout=int(config.get("timeout") or 30), follow_redirects=False)
                        else:
                            downloaded = client.get(f"{graph_base}/drives/{quote_plus(drive_id)}/items/{quote_plus(str(item['id']))}/content", follow_redirects=False)
                        downloaded.raise_for_status()
                        files.append((name, downloaded.content))
                        if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                            raise ValueError("云盘文件数量超过配置上限")
                    listing_url = str(payload.get("@odata.nextLink") or "")
        return _archive_payload(source_name, files, {"drive_id": drive_id, "path": remote_path})

    if source_type == "local_dir":
        root = _safe_local_path(str(config.get("path") or ""))
        recursive = bool(config.get("recursive", True))
        extensions = {
            str(value).casefold().lstrip(".")
            for value in (config.get("include_extensions") or [])
            if str(value).strip()
        }
        candidates = root.rglob("*") if recursive else root.glob("*")
        files: list[tuple[str, bytes]] = []
        for path in sorted(candidates):
            if not path.is_file() or path.is_symlink():
                continue
            if extensions and path.suffix.casefold().lstrip(".") not in extensions:
                continue
            files.append((path.relative_to(root).as_posix(), path.read_bytes()))
            if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                raise ValueError("本地目录文件数量超过配置上限")
        return _archive_payload(source_name, files, {"root": str(root), "recursive": recursive})

    if source_type in {"s3", "object_prefix"}:
        from minio import Minio

        endpoint = str(config.get("endpoint") or "").strip()
        if ":" in endpoint:
            host = endpoint.rsplit(":", 1)[0].strip("[]")
        else:
            host = endpoint
        _assert_network_target(host)
        bucket = str(config.get("bucket") or "").strip()
        prefix = str(config.get("prefix") or "").lstrip("/")
        client = Minio(
            endpoint,
            access_key=str(config.get("access_key") or ""),
            secret_key=secret or "",
            secure=bool(config.get("secure", False)),
            region=str(config.get("region") or "") or None,
        )
        if not client.bucket_exists(bucket):
            raise ValueError("S3 存储桶不存在或无权访问")
        files = []
        for item in client.list_objects(bucket, prefix=prefix, recursive=True):
            if item.is_dir:
                continue
            response = client.get_object(bucket, item.object_name)
            try:
                files.append((item.object_name, response.read()))
            finally:
                response.close()
                response.release_conn()
            if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                raise ValueError("对象数量超过配置上限")
        return _archive_payload(source_name, files, {"bucket": bucket, "prefix": prefix})

    if source_type in {"ftp", "ftps"}:
        host = str(config.get("host") or "").strip()
        _assert_network_target(host)
        port = int(config.get("port") or 21)
        client = FTP_TLS(timeout=int(config.get("timeout") or 30)) if source_type == "ftps" else FTP(timeout=int(config.get("timeout") or 30))
        client.connect(host, port)
        client.login(str(config.get("username") or "anonymous"), secret or "")
        if source_type == "ftps":
            client.prot_p()
        start = str(config.get("path") or "/")
        files: list[tuple[str, bytes]] = []

        def walk(remote: str, depth: int = 0) -> None:
            if depth > int(config.get("max_depth") or 8):
                return
            entries = list(client.mlsd(remote, facts=["type"]))
            for name, facts in entries:
                if name in {".", ".."}:
                    continue
                target = f"{remote.rstrip('/')}/{name}"
                if facts.get("type") == "dir" and bool(config.get("recursive", True)):
                    walk(target, depth + 1)
                elif facts.get("type") == "file":
                    output = io.BytesIO()
                    client.retrbinary(f"RETR {target}", output.write)
                    files.append((target.removeprefix(start).lstrip("/") or name, output.getvalue()))
                    if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                        raise ValueError("FTP 文件数量超过配置上限")

        try:
            walk(start)
        finally:
            try:
                client.quit()
            except Exception:
                client.close()
        return _archive_payload(source_name, files, {"host": host, "path": start, "protocol": source_type})

    if source_type == "sftp":
        import paramiko

        host = str(config.get("host") or "").strip()
        _assert_network_target(host)
        transport = paramiko.Transport((host, int(config.get("port") or 22)))
        transport.connect(username=str(config.get("username") or ""), password=secret or None)
        client = paramiko.SFTPClient.from_transport(transport)
        start = str(config.get("path") or ".")
        files: list[tuple[str, bytes]] = []

        def walk_sftp(remote: str, depth: int = 0) -> None:
            import stat

            if depth > int(config.get("max_depth") or 8):
                return
            for entry in client.listdir_attr(remote):
                target = f"{remote.rstrip('/')}/{entry.filename}"
                if stat.S_ISLNK(entry.st_mode):
                    continue
                if stat.S_ISDIR(entry.st_mode) and bool(config.get("recursive", True)):
                    walk_sftp(target, depth + 1)
                elif stat.S_ISREG(entry.st_mode):
                    with client.open(target, "rb") as stream:
                        files.append((target.removeprefix(start).lstrip("/") or entry.filename, stream.read()))
                    if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                        raise ValueError("SFTP 文件数量超过配置上限")

        try:
            walk_sftp(start)
        finally:
            client.close()
            transport.close()
        return _archive_payload(source_name, files, {"host": host, "path": start})

    if source_type == "webdav":
        url = str(config.get("url") or "").rstrip("/") + "/"
        username = str(config.get("username") or "") or None
        files: list[tuple[str, bytes]] = []
        with _http_client(url, secret=secret, username=username, timeout=int(config.get("timeout") or 30)) as client:
            response = client.request("PROPFIND", url, headers={"Depth": "infinity"})
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            for node in root.findall(".//{DAV:}href"):
                href = str(node.text or "")
                target = urljoin(url, href)
                if target.rstrip("/") == url.rstrip("/") or target.endswith("/"):
                    continue
                downloaded = client.get(target)
                downloaded.raise_for_status()
                relative = unquote(httpx.URL(target).path).removeprefix(httpx.URL(url).path).lstrip("/")
                files.append((relative, downloaded.content))
                if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                    raise ValueError("WebDAV 文件数量超过配置上限")
        return _archive_payload(source_name, files, {"url": url})

    if source_type == "smb":
        import smbclient

        server = str(config.get("server") or "").strip()
        _assert_network_target(server)
        smbclient.register_session(
            server,
            username=str(config.get("username") or ""),
            password=secret or "",
            port=int(config.get("port") or 445),
        )
        share = str(config.get("share") or "").strip("/\\")
        subpath = str(config.get("path") or "").strip("/\\")
        root = f"\\\\{server}\\{share}" + (f"\\{subpath}" if subpath else "")
        files: list[tuple[str, bytes]] = []

        def walk_smb(path: str, relative: str = "", depth: int = 0) -> None:
            if depth > int(config.get("max_depth") or 8):
                return
            for entry in smbclient.scandir(path):
                child_relative = f"{relative}/{entry.name}".lstrip("/")
                if entry.is_dir() and bool(config.get("recursive", True)):
                    walk_smb(entry.path, child_relative, depth + 1)
                elif entry.is_file():
                    with smbclient.open_file(entry.path, mode="rb") as stream:
                        files.append((child_relative, stream.read()))
                    if len(files) > int(config.get("max_files") or MAX_SOURCE_FILES):
                        raise ValueError("SMB 文件数量超过配置上限")

        walk_smb(root)
        smbclient.delete_session(server)
        return _archive_payload(source_name, files, {"server": server, "share": share, "path": subpath})

    raise ValueError(f"不支持的连接器类型：{source_type}")
