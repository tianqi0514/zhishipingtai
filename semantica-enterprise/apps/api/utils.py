from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
import unicodedata

from sqlalchemy import inspect

from packages.platform.models import ModelConfig, SourceConnector, User
from packages.platform.security import masked_secret


def serialize_row(row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in inspect(row.__class__).columns:
        value = getattr(row, column.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[column.key] = value

    if isinstance(row, ModelConfig):
        if row.provider in {"huggingface", "bge", "fastembed"} or (row.config or {}).get("local_runtime"):
            data["api_key_status"] = "本地运行 · 无需 API Key"
        elif (row.config or {}).get("credential_model_config_id"):
            data["api_key_status"] = "复用受管凭据"
        else:
            data["api_key_status"] = masked_secret(row.api_key_encrypted)
        data.pop("api_key_encrypted", None)
    if isinstance(row, SourceConnector):
        data["secret_status"] = masked_secret(row.secret_encrypted)
        data.pop("secret_encrypted", None)
    if isinstance(row, User):
        data.pop("password_hash", None)
    return data


def apply_patch(row: Any, values: dict[str, Any], allowed: set[str]) -> None:
    for key, value in values.items():
        if key in allowed:
            setattr(row, key, value)


def attachment_content_disposition(filename: str) -> str:
    """Build a safe RFC 5987 attachment header for Unicode filenames."""
    name = Path(str(filename or "download").replace("\r", "_").replace("\n", "_")).name
    suffix = "".join(Path(name).suffixes)[-32:]
    stem = name[: -len(suffix)] if suffix else name
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    ascii_stem = "".join(character for character in ascii_stem if character.isalnum() or character in "._-")
    fallback = f"{ascii_stem or 'download'}{suffix}".replace('"', "_").replace("\\", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
