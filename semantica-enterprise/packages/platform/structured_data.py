from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator
from urllib.parse import quote_plus

from sqlalchemy import MetaData, Table, and_, asc, create_engine, desc, func, inspect, or_, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from packages.platform.models import (
    ContentElement,
    DataPreviewPolicy,
    DataSourceSchemaVersion,
    Document,
    DocumentVersion,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
)
from packages.platform.security import decrypt_secret
from packages.semantica_adapter.ingest import _assert_network_target


FORBIDDEN_NAME_PARTS = {
    "password", "passwd", "secret", "token", "api_key", "apikey", "access_key",
    "private_key", "credential",
}
MASKED_NAME_PARTS = {
    "id_card": "id_card", "idcard": "id_card", "bank_card": "bank_card",
    "bankcard": "bank_card", "mobile": "phone", "phone": "phone", "email": "email",
}
TYPE_FAMILIES = {
    "integer": ("int", "serial"),
    "number": ("numeric", "decimal", "float", "double", "real", "money"),
    "datetime": ("timestamp", "datetime"),
    "date": ("date",),
    "boolean": ("bool", "bit"),
    "json": ("json",),
    "binary": ("bytea", "blob", "binary", "varbinary"),
}


class StructuredDataError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def database_row_key(source_id: str, object_id: str, primary_key: list[str], row: dict[str, Any]) -> str | None:
    values = [row.get(name) for name in primary_key]
    if not primary_key or any(value is None for value in values):
        return None
    return hashlib.sha256(canonical_json({
        "source_id": source_id,
        "object_id": object_id,
        "primary_key": primary_key,
        "values": values,
    }).encode("utf-8")).hexdigest()


def _name_tokens(name: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
    tokens = set(filter(None, normalized.split("_")))
    tokens.add(normalized)
    return tokens


def sensitive_suggestion(name: str) -> tuple[str, str | None]:
    tokens = _name_tokens(name)
    if tokens & FORBIDDEN_NAME_PARTS:
        return "blocked", None
    for part, rule in MASKED_NAME_PARTS.items():
        if part in tokens:
            return "masked", rule
    return "normal", None


def _connection_url(source: SourceConnector) -> tuple[str, str]:
    config = source.config or {}
    dialect = str(config.get("dialect") or "postgresql").casefold()
    if dialect not in {"postgresql", "mysql"}:
        raise StructuredDataError("UNSUPPORTED_DIALECT", "仅支持 MySQL 和 PostgreSQL")
    host = str(config.get("host") or "").strip()
    if not host:
        raise StructuredDataError("SOURCE_CONFIG_INVALID", "数据库主机未配置")
    _assert_network_target(host)
    username = quote_plus(str(config.get("username") or ""))
    password = quote_plus(decrypt_secret(source.secret_encrypted) or "")
    database = quote_plus(str(config.get("database") or ""))
    port = int(config.get("port") or (5432 if dialect == "postgresql" else 3306))
    driver = "postgresql+psycopg" if dialect == "postgresql" else "mysql+pymysql"
    return dialect, f"{driver}://{username}:{password}@{host}:{port}/{database}"


def create_source_engine(source: SourceConnector, *, timeout_seconds: int = 15) -> tuple[str, Engine]:
    dialect, url = _connection_url(source)
    connect_args: dict[str, Any]
    if dialect == "postgresql":
        connect_args = {"connect_timeout": max(1, min(timeout_seconds, 120))}
    else:
        connect_args = {
            "connect_timeout": max(1, min(timeout_seconds, 120)),
            "read_timeout": max(1, min(timeout_seconds, 120)),
            "write_timeout": max(1, min(timeout_seconds, 120)),
        }
    return dialect, create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=1,
        pool_timeout=max(1, min(timeout_seconds, 120)),
        connect_args=connect_args,
    )


def _type_family(type_name: str) -> str:
    lowered = type_name.casefold()
    for family, parts in TYPE_FAMILIES.items():
        if any(part in lowered for part in parts):
            return family
    return "string"


def _json_value(value: Any, *, max_text_length: int = 500, allow_full: bool = False) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {
            "kind": "binary",
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, (dict, list, tuple)):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    result = str(value)
    if not allow_full and len(result) > max_text_length:
        return {"text": result[:max_text_length], "truncated": True, "length": len(result)}
    return result


def _mask(value: Any, rule: str) -> Any:
    if value is None:
        return None
    raw = str(value)
    if rule == "email" and "@" in raw:
        local, domain = raw.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if rule == "phone":
        digits = re.sub(r"\D", "", raw)
        return f"{digits[:3]}****{digits[-4:]}" if len(digits) >= 7 else "***"
    if rule == "id_card":
        return f"{raw[:4]}**********{raw[-4:]}" if len(raw) >= 8 else "***"
    if rule == "bank_card":
        return f"{raw[:4]} **** **** {raw[-4:]}" if len(raw) >= 8 else "***"
    return "***"


def _safe_comment(inspector, schema: str | None, table_name: str) -> str:
    try:
        return str((inspector.get_table_comment(table_name, schema=schema) or {}).get("text") or "")
    except Exception:
        return ""


def _object_id(schema: str | None, name: str) -> str:
    return f"{schema}.{name}" if schema else name


def _column_id(object_id: str, name: str) -> str:
    return f"{object_id}.{name}"


def _row_estimates(connection: Connection, dialect: str, database: str) -> dict[tuple[str | None, str], int | None]:
    estimates: dict[tuple[str | None, str], int | None] = {}
    try:
        if dialect == "postgresql":
            rows = connection.execute(text(
                "SELECT n.nspname, c.relname, GREATEST(c.reltuples, 0)::bigint "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind IN ('r','p','v','m') AND n.nspname NOT LIKE 'pg_%' "
                "AND n.nspname <> 'information_schema'"
            ))
            estimates.update({(row[0], row[1]): int(row[2]) for row in rows})
        else:
            rows = connection.execute(text(
                "SELECT table_schema, table_name, table_rows FROM information_schema.tables "
                "WHERE table_schema=:database"
            ), {"database": database})
            estimates.update({(row[0], row[1]): int(row[2] or 0) for row in rows})
    except Exception:
        pass
    return estimates


def _sample_rows(engine: Engine, schema: str | None, table_name: str) -> list[dict[str, Any]]:
    metadata = MetaData()
    try:
        table = Table(table_name, metadata, schema=schema, autoload_with=engine)
        selected = [column for column in table.columns if sensitive_suggestion(column.name)[0] == "normal"]
        if not selected:
            return []
        with engine.connect() as connection:
            return [
                {column.name: _json_value(row[column.name], max_text_length=120) for column in selected}
                for row in connection.execute(select(*selected).limit(3)).mappings()
            ]
    except Exception:
        return []


def discover_catalog(source: SourceConnector) -> dict[str, Any]:
    if source.source_type != "database":
        raise StructuredDataError("SOURCE_TYPE_INVALID", "只有数据库数据源支持结构发现")
    config = source.config or {}
    dialect, engine = create_source_engine(source, timeout_seconds=int(config.get("timeout") or 20))
    database = str(config.get("database") or "")
    schema = str(config.get("schema") or "").strip() or (None if dialect == "mysql" else "public")
    include = set(config.get("include_tables") or [])
    exclude = set(config.get("exclude_tables") or [])
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names(schema=schema)
        views = inspector.get_view_names(schema=schema)
        estimates: dict[tuple[str | None, str], int | None]
        with engine.connect() as connection:
            estimates = _row_estimates(connection, dialect, database)
        objects: list[dict[str, Any]] = []
        for kind, names in (("table", tables), ("view", views)):
            for name in sorted(names):
                qualified = _object_id(schema, name)
                if include and name not in include and qualified not in include:
                    continue
                if name in exclude or qualified in exclude:
                    continue
                columns = inspector.get_columns(name, schema=schema)
                primary_key = list((inspector.get_pk_constraint(name, schema=schema) or {}).get("constrained_columns") or [])
                try:
                    uniques = inspector.get_unique_constraints(name, schema=schema)
                except Exception:
                    uniques = []
                try:
                    foreign_keys = inspector.get_foreign_keys(name, schema=schema)
                except Exception:
                    foreign_keys = []
                try:
                    indexes = inspector.get_indexes(name, schema=schema)
                except Exception:
                    indexes = []
                sample_rows = _sample_rows(engine, schema, name) if bool(config.get("schema_sample_values", True)) else []
                row_estimate = estimates.get((schema or database, name))
                # PostgreSQL exposes reltuples=0 until ANALYZE has collected statistics.
                # A successful sample proves that such a zero is an unknown estimate,
                # not an empty table.  Keep true empty tables at zero while preventing
                # the UI and API consumers from reporting populated tables as empty.
                if row_estimate == 0 and sample_rows:
                    row_estimate = None
                column_rows = []
                for column in columns:
                    sensitivity, rule = sensitive_suggestion(str(column["name"]))
                    column_rows.append({
                        "id": _column_id(qualified, str(column["name"])),
                        "name": str(column["name"]),
                        "type": str(column.get("type") or "unknown"),
                        "type_family": _type_family(str(column.get("type") or "unknown")),
                        "nullable": bool(column.get("nullable", True)),
                        "default": str(column.get("default")) if column.get("default") is not None else None,
                        "comment": str(column.get("comment") or ""),
                        "primary_key": column["name"] in primary_key,
                        "unique": any(column["name"] in (item.get("column_names") or []) for item in uniques),
                        "sensitivity": sensitivity,
                        "masking_rule": rule,
                        "sample_values": [row.get(column["name"]) for row in sample_rows if column["name"] in row][:3],
                    })
                objects.append({
                    "id": qualified,
                    "schema": schema,
                    "name": name,
                    "kind": kind,
                    "comment": _safe_comment(inspector, schema, name),
                    "row_estimate": row_estimate,
                    "columns": column_rows,
                    "primary_key": primary_key,
                    "unique_constraints": [
                        {"name": item.get("name"), "column_names": item.get("column_names") or []}
                        for item in uniques
                    ],
                    "foreign_keys": [
                        {
                            "name": item.get("name"),
                            "constrained_columns": item.get("constrained_columns") or [],
                            "referred_schema": item.get("referred_schema"),
                            "referred_table": item.get("referred_table"),
                            "referred_columns": item.get("referred_columns") or [],
                        }
                        for item in foreign_keys
                    ],
                    "indexes": [
                        {
                            "name": item.get("name"),
                            "column_names": item.get("column_names") or [],
                            "unique": bool(item.get("unique", False)),
                        }
                        for item in indexes
                    ],
                })
        normalized = {
            "schema": "chuanshen.data-source-schema/v1",
            "source_id": source.id,
            "dialect": dialect,
            "database": database,
            "default_schema": schema,
            "objects": objects,
        }
        structural = {
            **normalized,
            "objects": [
                {
                    **{key: value for key, value in item.items() if key not in {"row_estimate"}},
                    "columns": [
                        {key: value for key, value in column.items() if key != "sample_values"}
                        for column in item["columns"]
                    ],
                }
                for item in objects
            ],
        }
        normalized["schema_fingerprint"] = fingerprint(structural)
        return normalized
    finally:
        engine.dispose()


def schema_diff(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return {
            "initial": True,
            "added_objects": [item["id"] for item in current.get("objects") or []],
            "removed_objects": [], "added_columns": [], "removed_columns": [],
            "type_changes": [], "primary_key_changes": [], "foreign_key_changes": [],
            "comment_changes": [], "rename_candidates": [], "breaking": False,
        }
    old_objects = {item["id"]: item for item in previous.get("objects") or []}
    new_objects = {item["id"]: item for item in current.get("objects") or []}
    result: dict[str, Any] = {
        "initial": False,
        "added_objects": sorted(set(new_objects) - set(old_objects)),
        "removed_objects": sorted(set(old_objects) - set(new_objects)),
        "added_columns": [], "removed_columns": [], "type_changes": [],
        "primary_key_changes": [], "foreign_key_changes": [], "comment_changes": [],
        "rename_candidates": [],
    }
    for object_id in sorted(set(old_objects) & set(new_objects)):
        old = old_objects[object_id]
        new = new_objects[object_id]
        old_cols = {item["id"]: item for item in old.get("columns") or []}
        new_cols = {item["id"]: item for item in new.get("columns") or []}
        added = sorted(set(new_cols) - set(old_cols))
        removed = sorted(set(old_cols) - set(new_cols))
        result["added_columns"].extend(added)
        result["removed_columns"].extend(removed)
        for column_id in sorted(set(old_cols) & set(new_cols)):
            if old_cols[column_id].get("type") != new_cols[column_id].get("type"):
                result["type_changes"].append({
                    "column_id": column_id,
                    "from": old_cols[column_id].get("type"),
                    "to": new_cols[column_id].get("type"),
                    "compatible": old_cols[column_id].get("type_family") == new_cols[column_id].get("type_family"),
                })
            if old_cols[column_id].get("comment") != new_cols[column_id].get("comment"):
                result["comment_changes"].append(column_id)
        if old.get("primary_key") != new.get("primary_key"):
            result["primary_key_changes"].append({"object_id": object_id, "from": old.get("primary_key"), "to": new.get("primary_key")})
        if canonical_json(old.get("foreign_keys") or []) != canonical_json(new.get("foreign_keys") or []):
            result["foreign_key_changes"].append(object_id)
        for removed_id in removed:
            removed_column = old_cols[removed_id]
            candidates = [
                added_id for added_id in added
                if new_cols[added_id].get("type_family") == removed_column.get("type_family")
                and new_cols[added_id].get("nullable") == removed_column.get("nullable")
            ]
            if len(candidates) == 1:
                result["rename_candidates"].append({"from": removed_id, "to": candidates[0]})
    result["breaking"] = bool(
        result["removed_objects"] or result["removed_columns"] or result["primary_key_changes"]
        or result["foreign_key_changes"] or any(not item["compatible"] for item in result["type_changes"])
    )
    return result


def current_schema(db: Session, source_id: str) -> DataSourceSchemaVersion | None:
    return db.scalar(
        select(DataSourceSchemaVersion).where(
            DataSourceSchemaVersion.source_id == source_id,
            DataSourceSchemaVersion.status == "current",
            DataSourceSchemaVersion.deleted_at.is_(None),
        ).order_by(DataSourceSchemaVersion.version_number.desc()).limit(1)
    )


def persist_discovery(db: Session, source: SourceConnector, catalog: dict[str, Any]) -> tuple[DataSourceSchemaVersion, bool]:
    existing = current_schema(db, source.id)
    if existing and existing.schema_fingerprint == catalog["schema_fingerprint"]:
        existing.catalog = catalog
        existing.discovered_at = datetime.now(timezone.utc)
        db.flush()
        return existing, False
    version_number = (db.scalar(
        select(func.max(DataSourceSchemaVersion.version_number)).where(
            DataSourceSchemaVersion.source_id == source.id
        )
    ) or 0) + 1
    diff = schema_diff(existing.catalog if existing else None, catalog)
    if existing:
        existing.status = "superseded"
    objects = catalog.get("objects") or []
    row = DataSourceSchemaVersion(
        tenant_id=source.tenant_id,
        space_id=source.space_id,
        source_id=source.id,
        version_number=version_number,
        schema_fingerprint=catalog["schema_fingerprint"],
        catalog=catalog,
        diff_from_previous=diff,
        status="current",
        object_count=len(objects),
        column_count=sum(len(item.get("columns") or []) for item in objects),
        primary_key_count=sum(bool(item.get("primary_key")) for item in objects),
        foreign_key_count=sum(len(item.get("foreign_keys") or []) for item in objects),
    )
    db.add(row)
    db.flush()
    if existing:
        _mark_stale_mappings(db, source.id, existing, row)
    return row, True


def _mapping_references(manifest: dict[str, Any]) -> tuple[set[str], set[str]]:
    objects: set[str] = set()
    columns: set[str] = set()
    for entity in manifest.get("entities") or []:
        for fragment in entity.get("fragments") or []:
            objects.add(str(fragment.get("object_id") or ""))
            columns.update(str(item) for item in fragment.get("identity_column_ids") or [])
            if fragment.get("display_column_id"):
                columns.add(str(fragment["display_column_id"]))
    for attribute in manifest.get("attributes") or []:
        columns.add(str(attribute.get("column_id") or ""))
    for relation in manifest.get("relationships") or []:
        for predicate in relation.get("predicates") or []:
            for side in ("left", "right"):
                value = predicate.get(side) or {}
                objects.add(str(value.get("object_id") or ""))
                columns.add(str(value.get("column_id") or ""))
    return {item for item in objects if item}, {item for item in columns if item}


def _mark_stale_mappings(
    db: Session,
    source_id: str,
    previous: DataSourceSchemaVersion,
    current: DataSourceSchemaVersion,
) -> None:
    diff = current.diff_from_previous or {}
    removed_objects = set(diff.get("removed_objects") or [])
    removed_columns = set(diff.get("removed_columns") or [])
    incompatible = {item["column_id"] for item in diff.get("type_changes") or [] if not item.get("compatible")}
    pk_objects = {item["object_id"] for item in diff.get("primary_key_changes") or []}
    fk_objects = set(diff.get("foreign_key_changes") or [])
    for version in db.scalars(select(SemanticMappingVersion).where(
        SemanticMappingVersion.source_id == source_id,
        SemanticMappingVersion.status == "active",
        SemanticMappingVersion.deleted_at.is_(None),
    )):
        objects, columns = _mapping_references(version.manifest or {})
        reasons = sorted(
            (objects & (removed_objects | pk_objects | fk_objects))
            | (columns & (removed_columns | incompatible))
        )
        if reasons:
            version.status = "stale"
            version.validation_report = {
                **(version.validation_report or {}),
                "ok": False,
                "stale": True,
                "schema_version_id": current.id,
                "errors": [f"Schema 变化影响已激活映射：{item}" for item in reasons],
            }
            mapping_set = db.get(SemanticMappingSet, version.mapping_set_id)
            if mapping_set:
                mapping_set.status = "stale"


def get_or_create_preview_policy(db: Session, source: SourceConnector) -> DataPreviewPolicy:
    policy = db.scalar(select(DataPreviewPolicy).where(
        DataPreviewPolicy.source_id == source.id,
        DataPreviewPolicy.deleted_at.is_(None),
    ))
    if policy is None:
        policy = DataPreviewPolicy(
            tenant_id=source.tenant_id,
            space_id=source.space_id,
            source_id=source.id,
        )
        db.add(policy)
        db.flush()
    return policy


def catalog_object(schema: DataSourceSchemaVersion, object_id: str) -> dict[str, Any]:
    found = next((item for item in (schema.catalog or {}).get("objects") or [] if item.get("id") == object_id), None)
    if found is None:
        raise StructuredDataError("OBJECT_NOT_FOUND", "表或视图不在当前发现结果中", status_code=404)
    return found


def _policy_column_access(policy: DataPreviewPolicy, object_row: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    object_id = object_row["id"]
    allowed_objects = set(policy.allowed_objects or [])
    if (allowed_objects and object_id not in allowed_objects) or object_id in set(policy.denied_objects or []):
        raise StructuredDataError("OBJECT_PREVIEW_DENIED", "该表或视图未开放预览", status_code=403)
    allowed = set((policy.allowed_columns or {}).get(object_id) or [])
    forced_sensitive = set((policy.sensitive_columns or {}).get(object_id) or [])
    masking = dict(policy.masking_rules or {})
    visible: list[dict[str, Any]] = []
    rules: dict[str, str] = {}
    for column in object_row.get("columns") or []:
        column_id = column["id"]
        sensitivity = column.get("sensitivity") or "normal"
        if column_id in forced_sensitive:
            sensitivity = "blocked"
        if allowed and column_id not in allowed:
            continue
        if sensitivity == "blocked":
            continue
        visible.append(column)
        rule = masking.get(column_id) or column.get("masking_rule")
        if sensitivity == "masked" or rule:
            rules[column_id] = rule or "redact"
    return visible, rules


def _coerce_filter_value(family: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        if family == "integer" and not isinstance(value, bool):
            return int(value)
        if family == "number" and not isinstance(value, (int, float, Decimal)):
            return Decimal(str(value))
        if family == "date" and isinstance(value, str):
            return date.fromisoformat(value)
        if family == "datetime" and isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if family == "boolean" and isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
            raise ValueError("invalid boolean")
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise StructuredDataError("FILTER_VALUE_INVALID", f"筛选值与字段类型不兼容：{family}") from exc
    return value


def _filter_expression(column, family: str, operator: str, value: Any, upper: Any):
    if operator == "in":
        if not isinstance(value, list) or not value or len(value) > 100:
            raise StructuredDataError("FILTER_INVALID", "多选筛选必须提供 1 到 100 个值")
        value = [_coerce_filter_value(family, item) for item in value]
    else:
        value = _coerce_filter_value(family, value)
    upper = _coerce_filter_value(family, upper)
    if operator == "eq": return column == value
    if operator == "ne": return column != value
    if operator == "gt": return column > value
    if operator == "gte": return column >= value
    if operator == "lt": return column < value
    if operator == "lte": return column <= value
    if operator == "is_null": return column.is_(None)
    if operator == "is_not_null": return column.is_not(None)
    if operator == "between": return column.between(value, upper)
    if operator == "in":
        return column.in_(value)
    if family != "string":
        raise StructuredDataError("FILTER_INVALID", "该字段类型不支持文本筛选")
    escaped = str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if operator == "contains": return column.like(f"%{escaped}%", escape="\\")
    if operator == "not_contains": return ~column.like(f"%{escaped}%", escape="\\")
    if operator == "starts_with": return column.like(f"{escaped}%", escape="\\")
    raise StructuredDataError("FILTER_OPERATOR_INVALID", "筛选操作不受支持")


@contextmanager
def readonly_connection(engine: Engine, dialect: str, timeout_seconds: int) -> Iterator[Connection]:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        if dialect == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {max(1, timeout_seconds) * 1000}")
        elif dialect == "mysql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def _table_from_catalog(engine: Engine, object_row: dict[str, Any]) -> Table:
    return Table(
        object_row["name"],
        MetaData(),
        schema=object_row.get("schema"),
        autoload_with=engine,
    )


def preview_live(
    source: SourceConnector,
    schema: DataSourceSchemaVersion,
    policy: DataPreviewPolicy,
    *,
    object_id: str,
    page: int,
    page_size: int,
    order_by: str | None,
    order_direction: str,
    filters: list[dict[str, Any]],
) -> dict[str, Any]:
    if not policy.live_preview_enabled:
        raise StructuredDataError("LIVE_PREVIEW_DISABLED", "该数据源未启用实时预览", status_code=403)
    if len(filters) > policy.max_filter_conditions:
        raise StructuredDataError("FILTER_LIMIT_EXCEEDED", "筛选条件数量超过策略上限")
    page_size = min(page_size, policy.max_page_size, 100)
    object_row = catalog_object(schema, object_id)
    visible, masking_rules = _policy_column_access(policy, object_row)
    if not visible:
        raise StructuredDataError("NO_VISIBLE_COLUMNS", "该表没有可预览字段", status_code=403)
    dialect, engine = create_source_engine(source, timeout_seconds=policy.query_timeout_seconds)
    started = time.perf_counter()
    try:
        table = _table_from_catalog(engine, object_row)
        by_id = {item["id"]: item for item in object_row.get("columns") or []}
        visible_ids = {item["id"] for item in visible}
        selected_names = [item["name"] for item in visible]
        for primary_key in object_row.get("primary_key") or []:
            if primary_key in table.c and primary_key not in selected_names:
                selected_names.append(primary_key)
        selected = [table.c[name] for name in selected_names]
        statement = select(*selected)
        conditions = []
        for item in filters:
            column_id = item["column_id"]
            if column_id not in visible_ids or column_id in masking_rules:
                raise StructuredDataError("FILTER_COLUMN_DENIED", "该字段不允许用于筛选", status_code=403)
            column_row = by_id[column_id]
            conditions.append(_filter_expression(
                table.c[column_row["name"]], column_row.get("type_family") or "string",
                item["operator"], item.get("value"), item.get("upper"),
            ))
        if conditions:
            statement = statement.where(and_(*conditions))
        warnings: list[str] = []
        target_order = order_by or (policy.default_order or {}).get(object_id)
        if target_order:
            if target_order not in visible_ids or target_order in masking_rules:
                raise StructuredDataError("ORDER_COLUMN_DENIED", "该字段不允许用于排序", status_code=403)
            order_columns = [table.c[by_id[target_order]["name"]]]
        elif object_row.get("primary_key"):
            order_columns = [table.c[name] for name in object_row["primary_key"] if name in table.c]
        else:
            order_columns = [table.c[item["name"]] for item in visible if item.get("type_family") not in {"binary", "json"}][:3]
            warnings.append("该对象没有主键，跨页查看时数据顺序可能随源库变化")
        if order_columns:
            order = desc if order_direction == "desc" else asc
            statement = statement.order_by(*(order(column) for column in order_columns))
        statement = statement.limit(page_size + 1).offset((page - 1) * page_size)
        with readonly_connection(engine, dialect, policy.query_timeout_seconds) as connection:
            raw_rows = list(connection.execute(statement).mappings())
        has_next = len(raw_rows) > page_size
        rows: list[dict[str, Any]] = []
        row_keys: list[str | None] = []
        result_bytes = 0
        truncated_by_bytes = False
        for raw in raw_rows[:page_size]:
            row: dict[str, Any] = {}
            for column in visible:
                column_id = column["id"]
                value = raw[column["name"]]
                if column_id in masking_rules:
                    value = _mask(value, masking_rules[column_id])
                else:
                    value = _json_value(
                        value,
                        max_text_length=policy.max_text_length,
                        allow_full=policy.allow_full_cell,
                    )
                row[column["name"]] = value
            encoded = len(canonical_json(row).encode("utf-8"))
            if result_bytes + encoded > policy.max_result_bytes:
                truncated_by_bytes = True
                break
            result_bytes += encoded
            rows.append(row)
            row_keys.append(database_row_key(
                source.id,
                object_id,
                list(object_row.get("primary_key") or []),
                dict(raw),
            ))
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return {
            "mode": "live",
            "source_id": source.id,
            "object": object_row,
            "schema_version_id": schema.id,
            "schema_fingerprint": schema.schema_fingerprint,
            "columns": visible,
            "rows": rows,
            "row_keys": row_keys,
            "page": page,
            "page_size": page_size,
            "current_page_rows": len(rows),
            "row_estimate": object_row.get("row_estimate"),
            "has_next": has_next or truncated_by_bytes,
            "truncated": truncated_by_bytes,
            "result_bytes": result_bytes,
            "query_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms,
            "warnings": warnings,
        }
    except StructuredDataError:
        raise
    except Exception as exc:
        raise StructuredDataError("DATABASE_PREVIEW_FAILED", f"实时数据预览失败：{type(exc).__name__}") from exc
    finally:
        engine.dispose()


def preview_snapshot(
    db: Session,
    source: SourceConnector,
    schema: DataSourceSchemaVersion,
    policy: DataPreviewPolicy,
    *,
    object_id: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    object_row = catalog_object(schema, object_id)
    visible, masking_rules = _policy_column_access(policy, object_row)
    document = db.scalar(select(Document).where(
        Document.source_id == source.id,
        Document.deleted_at.is_(None),
    ).order_by(Document.created_at.desc()).limit(1))
    if not document:
        raise StructuredDataError("SNAPSHOT_UNAVAILABLE", "该数据源尚未完成知识同步", status_code=404)
    version = db.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
    if version is None:
        # Source synchronization, parsing and knowledge publication are
        # intentionally separate durable stages.  A parsed database snapshot
        # is safe to preview before it becomes the current searchable version.
        version = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.deleted_at.is_(None),
        ).order_by(DocumentVersion.created_at.desc()).limit(1))
    if version is None:
        raise StructuredDataError("SNAPSHOT_UNAVAILABLE", "该数据源尚未生成同步版本", status_code=404)
    elements = list(db.scalars(select(ContentElement).where(
        ContentElement.version_id == version.id,
        ContentElement.element_type == "record",
        ContentElement.deleted_at.is_(None),
    ).order_by(ContentElement.ordinal)))
    if not elements:
        raise StructuredDataError("SNAPSHOT_PROCESSING", "同步快照仍在解析，请稍后重试", status_code=409)
    matching = [item for item in elements if (item.element_metadata or {}).get("object_id") == object_id]
    start = (page - 1) * page_size
    rows = []
    row_keys = []
    for item in matching[start:start + page_size]:
        metadata = item.element_metadata or {}
        values = metadata.get("row")
        if not isinstance(values, dict):
            try:
                values = json.loads(item.text)
            except Exception:
                values = {"content": item.text}
        safe_row: dict[str, Any] = {}
        for column in visible:
            value = values.get(column["name"])
            if column["id"] in masking_rules:
                value = _mask(value, masking_rules[column["id"]])
            else:
                value = _json_value(
                    value,
                    max_text_length=policy.max_text_length,
                    allow_full=policy.allow_full_cell,
                )
            safe_row[column["name"]] = value
        rows.append(safe_row)
        row_keys.append(metadata.get("row_key"))
    return {
        "mode": "snapshot",
        "source_id": source.id,
        "object": object_row,
        "schema_version_id": schema.id,
        "columns": visible,
        "rows": rows,
        "row_keys": row_keys,
        "page": page,
        "page_size": page_size,
        "current_page_rows": len(rows),
        "row_estimate": len(matching),
        "has_next": start + page_size < len(matching),
        "truncated": False,
        "query_time": datetime.now(timezone.utc).isoformat(),
        "snapshot_time": version.created_at.isoformat(),
        "document_id": document.id,
        "version_id": version.id,
        "warnings": [],
    }


def exact_count(
    source: SourceConnector,
    schema: DataSourceSchemaVersion,
    policy: DataPreviewPolicy,
    *,
    object_id: str,
    filters: list[dict[str, Any]],
) -> dict[str, Any]:
    if not policy.allow_exact_count:
        raise StructuredDataError("EXACT_COUNT_DISABLED", "该数据源未开放精确行数统计", status_code=403)
    object_row = catalog_object(schema, object_id)
    visible, masking_rules = _policy_column_access(policy, object_row)
    dialect, engine = create_source_engine(source, timeout_seconds=policy.query_timeout_seconds)
    started = time.perf_counter()
    try:
        table = _table_from_catalog(engine, object_row)
        by_id = {item["id"]: item for item in visible}
        conditions = []
        for item in filters:
            column_id = item["column_id"]
            if column_id not in by_id or column_id in masking_rules:
                raise StructuredDataError("FILTER_COLUMN_DENIED", "该字段不允许用于筛选", status_code=403)
            column_row = by_id[column_id]
            conditions.append(_filter_expression(
                table.c[column_row["name"]], column_row.get("type_family") or "string",
                item["operator"], item.get("value"), item.get("upper"),
            ))
        statement = select(func.count()).select_from(table)
        if conditions:
            statement = statement.where(and_(*conditions))
        with readonly_connection(engine, dialect, policy.query_timeout_seconds) as connection:
            count = int(connection.scalar(statement) or 0)
        return {"object_id": object_id, "count": count, "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    finally:
        engine.dispose()


def inspect_distinct_values(
    source: SourceConnector,
    schema: DataSourceSchemaVersion,
    policy: DataPreviewPolicy,
    *,
    object_id: str,
    column_id: str,
    search: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Return a bounded set of safe values for one mapped attribute."""
    object_row = catalog_object(schema, object_id)
    visible, masking_rules = _policy_column_access(policy, object_row)
    by_id = {item["id"]: item for item in visible}
    column_row = by_id.get(column_id)
    if column_row is None or column_id in masking_rules or column_row.get("sensitivity") != "normal":
        raise StructuredDataError("VALUE_INSPECTION_DENIED", "该字段不允许探查取值", status_code=403)
    limit = max(1, min(int(limit), 100))
    dialect, engine = create_source_engine(source, timeout_seconds=policy.query_timeout_seconds)
    started = time.perf_counter()
    try:
        table = _table_from_catalog(engine, object_row)
        selected = table.c[column_row["name"]]
        statement = select(selected).where(selected.is_not(None)).distinct()
        if search:
            if column_row.get("type_family") != "string":
                raise StructuredDataError("VALUE_SEARCH_UNSUPPORTED", "只有文本字段支持按输入值探查")
            statement = statement.where(_filter_expression(
                selected, "string", "contains", search, None,
            ))
        statement = statement.order_by(asc(selected)).limit(limit)
        with readonly_connection(engine, dialect, policy.query_timeout_seconds) as connection:
            values = [
                _json_value(row[0], max_text_length=policy.max_text_length, allow_full=False)
                for row in connection.execute(statement)
            ]
        return {
            "attribute_column_id": column_id,
            "data_type": column_row.get("type_family"),
            "values": values,
            "matched": bool(values),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "warnings": [],
        }
    except StructuredDataError:
        raise
    except Exception as exc:
        raise StructuredDataError("VALUE_INSPECTION_FAILED", f"字段取值探查失败：{type(exc).__name__}") from exc
    finally:
        engine.dispose()
