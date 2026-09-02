from __future__ import annotations

from datetime import datetime
from typing import Any

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
