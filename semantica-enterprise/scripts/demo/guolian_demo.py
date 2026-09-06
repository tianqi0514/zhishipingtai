#!/usr/bin/env python3
"""Shared, safe orchestration helpers for the Guolian full-platform demo.

The helpers in this module intentionally use the platform's public REST API.
They never open the platform database, object store or search engines directly,
and they never persist credentials.  All create operations are idempotent by a
stable code/name within the dedicated ``guolian-enterprise-demo`` space.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
import unicodedata
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

import httpx

from scripts.demo.guolian_structured_queries import guolian_governed_query_templates


ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = ROOT / "demo" / "guolian"
SPACE_CODE = "guolian-enterprise-demo"
SPACE_NAME = "国联集团组织级知识底座演示空间"
DEMO_NOTICE = "演示数据，不代表国联集团真实经营数据。"
ONTOLOGY_CODE = "guolian-enterprise-demo-ontology"
MEDIA_POLICY_NAME = "国联集团演示多模态策略"
DEMO_ROUTING_POLICY_NAME = "国联集团演示模型路由"
DEMO_ROUTING_DESCRIPTION = f"国联集团现场演示使用的已验证模型路由。{DEMO_NOTICE}"
DEMO_LLM_SCENES: tuple[str, ...] = (
    "agent_chat",
    "semantic_extract",
    "document_governance",
    "structured_query",
)

ROLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "code": "guolian-demo-knowledge-admin",
        "name": "演示·集团知识管理员",
        "permissions": [
            "document.*", "source.*", "job.read", "search", "answer",
            "application.manage", "audit.read",
        ],
        "space_permission": "manage",
    },
    {
        "code": "guolian-demo-curator",
        "name": "演示·集团业务知识专员",
        "permissions": [
            "document.create", "document.read", "document.update", "source.read",
            "job.read", "search", "answer",
        ],
        "space_permission": "write",
    },
    {
        "code": "guolian-demo-business-user",
        "name": "演示·集团普通业务用户",
        "permissions": ["document.read", "search", "answer"],
        "space_permission": "read",
    },
)

USER_SPECS: tuple[dict[str, Any], ...] = (
    {
        "username": "guolian_demo_admin",
        "display_name": "演示·集团知识管理员",
        "role_code": "guolian-demo-knowledge-admin",
        "is_admin": True,
    },
    {
        "username": "guolian_demo_curator",
        "display_name": "演示·集团业务知识专员",
        "role_code": "guolian-demo-curator",
        "is_admin": False,
    },
    {
        "username": "guolian_demo_user",
        "display_name": "演示·集团普通业务用户",
        "role_code": "guolian-demo-business-user",
        "is_admin": False,
    },
)

ONTOLOGY_TERMS: tuple[tuple[str, str, str], ...] = (
    ("organization", "组织", "class"),
    ("department", "部门", "class"),
    ("policy", "制度", "class"),
    ("supplier", "供应商", "class"),
    ("product", "产品", "class"),
    ("project", "项目", "class"),
    ("system", "系统", "class"),
    ("contract", "合同", "class"),
    ("purchase_order", "采购订单", "class"),
    ("purchase_order_item", "采购订单明细", "class"),
    ("approval_record", "审批记录", "class"),
    ("procurement_target", "采购目标", "class"),
    ("project_product", "项目产品关联", "class"),
    ("risk_event", "风险事件", "event"),
    ("meeting", "会议", "event"),
    ("task", "任务", "class"),
    ("name", "名称", "property"),
    ("code", "编码", "property"),
    ("amount", "采购金额", "property"),
    ("date", "日期", "property"),
    ("status", "状态", "property"),
    ("decision", "审批结论", "property"),
    ("risk_level", "风险等级", "property"),
    ("manage", "管理", "relation"),
    ("responsible_for", "负责", "relation"),
    ("applies_to", "适用于", "relation"),
    ("supply", "供应", "relation"),
    ("use", "用于", "relation"),
    ("depend_on", "依赖", "relation"),
    ("has_risk", "存在风险", "relation"),
    ("affected_by", "受到影响", "relation"),
    ("involve", "涉及", "relation"),
    ("belongs_to", "所属", "relation"),
    ("correspond_to", "对应", "relation"),
)

DEMO_DOCUMENT_NAMES: tuple[str, ...] = (
    "集团本部采购实施细则（2025演示现行版）.md",
    "集团本部采购实施细则（2023演示旧版）.md",
    "智慧流程中枢项目建设方案（演示版）.pdf",
    "供应商现场评估记录（演示版）-扫描件.pdf",
    "智慧流程中枢项目周报（演示版）.docx",
    "集团知识底座建设汇报（演示版）.pptx",
    "供应商风险台账（演示版）.xlsx",
    "采购订单明细（演示版）.csv",
    "项目系统依赖关系（演示版）.json",
    "供应商风险事件（演示版）.jsonl",
    "采购工作指引（演示版）.html",
    "东方智造交付延期通知（演示版）.eml",
    "项目总体架构图（演示版）.png",
    "扫描采购审批单（演示版）.jpg",
    "供应商评估表截图（演示版）.png",
    "智慧流程中枢项目例会（演示版）.wav",
    "智慧流程中枢项目介绍（演示版）.mp4",
    "国联集团演示知识包.zip",
    "智慧流程中枢建设说明（演示版）.rst",
    "智慧流程中枢项目例会要点（演示版）.txt",
    "规则推演前提事实（演示版）.txt",
    "组织与项目关系（演示版）.xml",
    "知识加工策略（演示版）.yaml",
    "procurement_risk_rules.py",
)

DATABASE_TABLES: tuple[str, ...] = (
    "org_units", "departments", "suppliers", "supplier_contacts", "products", "projects",
    "project_products", "purchase_orders", "purchase_order_items",
    "approval_records", "contracts", "risk_events", "procurement_targets",
    "system_dependencies", "policy_applicability", "archived_projects",
)
DATABASE_VIEWS: tuple[str, ...] = ("procurement_order_overview",)
DATABASE_OBJECTS: tuple[str, ...] = DATABASE_TABLES + DATABASE_VIEWS

SOURCE_FIXTURE_BASE_URL = "http://source-fixture:8088"
SOURCE_FIXTURE_GIT_URL = "git://source-fixture:9418/guolian-demo.git"
SOURCE_FIXTURE_BUCKET = "guolian-enterprise-demo-sources"
SOURCE_FIXTURE_PREFIX = "knowledge/"
SOURCE_FIXTURE_LOCAL_ROOT = "/app/data/sources/guolian-enterprise-demo"

# Only deterministic, explicitly reviewed synthetic files may be copied to a
# connector fixture.  Never sweep the demo directory or the customer archive.
LOCAL_SOURCE_FIXTURE_FILES: tuple[str, ...] = (
    "智慧流程中枢项目例会要点（演示版）.txt",
    "知识加工策略（演示版）.yaml",
    "procurement_risk_rules.py",
)
OBJECT_SOURCE_FIXTURE_FILES: tuple[str, ...] = (
    "项目系统依赖关系（演示版）.json",
    "供应商风险事件（演示版）.jsonl",
    "采购工作指引（演示版）.html",
)

_SECRET_KEYS = re.compile(
    r"(?i)(api[_-]?key|password|passwd|secret|token|authorization|credential|private[_-]?key)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]+=*")
_URL_CREDENTIALS = re.compile(r"(?P<prefix>://[^:/\s]+:)[^@/\s]+@")


class DemoError(RuntimeError):
    """Expected orchestration failure with a safe, operator-facing message."""


class ApiLike(Protocol):
    def call(self, method: str, path: str, **kwargs: Any) -> Any: ...


def redact(value: Any, *, extra_secrets: Iterable[str] = ()) -> str:
    """Render and redact a value without echoing credentials."""

    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            rendered = repr(value)
    rendered = _BEARER.sub("Bearer ***", rendered)
    rendered = _URL_CREDENTIALS.sub(r"\g<prefix>***@", rendered)
    rendered = re.sub(
        r'(?i)(["\']?(?:api[_-]?key|password|passwd|secret|token|authorization|credential|private[_-]?key)["\']?\s*[:=]\s*)["\']?[^,}\]\s"\']+',
        r"\1***",
        rendered,
    )
    for secret in extra_secrets:
        if secret:
            rendered = rendered.replace(secret, "***")
    return rendered


def safe_public_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DemoError("API 地址必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password:
        raise DemoError("API 地址不能包含用户名或密码")
    return value.rstrip("/")


def env_first(*names: str, required: bool = False) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    if required:
        raise DemoError(f"缺少环境变量：{' / '.join(names)}")
    return None


def api_url_from_environment() -> str:
    return safe_public_url(
        env_first("GUOLIAN_DEMO_API_URL", "API_BASE")
        or "http://127.0.0.1:8080/api/v1"
    )


def admin_credentials_from_environment() -> tuple[str, str]:
    username = env_first("GUOLIAN_DEMO_ADMIN_USERNAME", "ADMIN_USERNAME") or "admin"
    password = env_first(
        "GUOLIAN_DEMO_ADMIN_PASSWORD", "ADMIN_PASSWORD", "BOOTSTRAP_ADMIN_PASSWORD",
        required=True,
    )
    assert password is not None
    return username, password


class SafeApiClient:
    """Authenticated API wrapper whose errors never print request payloads."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 60,
    ) -> None:
        self._secrets = tuple(value for value in (password,) if value)
        self.client = httpx.Client(
            base_url=safe_public_url(base_url),
            timeout=httpx.Timeout(30, read=timeout_seconds),
        )
        response = self.client.post("/auth/login", json={"username": username, "password": password})
        if not response.is_success:
            raise DemoError(f"平台登录失败（HTTP {response.status_code}）")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise DemoError("平台登录响应缺少访问令牌")
        self._secrets += (str(token),)
        self.client.headers["Authorization"] = f"Bearer {token}"

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "SafeApiClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        call_secrets = kwargs.pop("_redact_secrets", ())
        if isinstance(call_secrets, str):
            call_secrets = (call_secrets,)
        call_secrets = tuple(str(value) for value in call_secrets if value)
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DemoError(f"{method} {path} 请求失败：{type(exc).__name__}") from exc
        if not response.is_success:
            detail = redact(
                response.text[:1200],
                extra_secrets=(*self._secrets, *call_secrets),
            )
            raise DemoError(f"{method} {path} 返回 HTTP {response.status_code}：{detail}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise DemoError(f"{method} {path} 未返回合法 JSON") from exc


@dataclass(frozen=True)
class PlannedAction:
    phase: str
    action: str
    target: str
    destructive: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "action": self.action,
            "target": self.target,
            "destructive": self.destructive,
        }


@dataclass
class PrepareReport:
    space_id: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)

    def add(self, phase: str, action: str, target: str, status: str, **details: Any) -> None:
        safe_details = {
            key: value for key, value in details.items()
            if not _SECRET_KEYS.search(key)
        }
        self.actions.append(
            {"phase": phase, "action": action, "target": target, "status": status, **safe_details}
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": SPACE_CODE,
            "demo_data_notice": DEMO_NOTICE,
            "space_id": self.space_id,
            "actions": self.actions,
            "warnings": self.warnings,
            "resources": self.resources,
        }


def discover_demo_documents(root: Path = DEMO_ROOT) -> list[Path]:
    """Return only the explicit, customer-safe demo upload allowlist."""

    paths: list[Path] = []
    for name in DEMO_DOCUMENT_NAMES:
        candidate = root / name
        if candidate.is_file():
            paths.append(candidate)
    return paths


def missing_required_demo_documents(root: Path = DEMO_ROOT) -> list[str]:
    """Report missing headline assets; optional backup formats do not block prep."""

    required = {
        "集团本部采购实施细则（2025演示现行版）.md",
        "智慧流程中枢项目建设方案（演示版）.pdf",
        "供应商现场评估记录（演示版）-扫描件.pdf",
        "智慧流程中枢项目周报（演示版）.docx",
        "集团知识底座建设汇报（演示版）.pptx",
        "供应商风险台账（演示版）.xlsx",
        "采购订单明细（演示版）.csv",
        "项目系统依赖关系（演示版）.json",
        "东方智造交付延期通知（演示版）.eml",
        "项目总体架构图（演示版）.png",
        "智慧流程中枢项目例会（演示版）.wav",
        "智慧流程中枢项目介绍（演示版）.mp4",
        "规则推演前提事实（演示版）.txt",
    }
    return sorted(name for name in required if not (root / name).is_file())


def build_prepare_plan(root: Path = DEMO_ROOT) -> list[PlannedAction]:
    actions = [
        PlannedAction("preflight", "validate", "服务、模型、Ground Truth 与演示素材"),
        PlannedAction("identity", "ensure", "3 个演示角色和演示用户"),
        PlannedAction("space", "ensure", f"{SPACE_NAME} ({SPACE_CODE})"),
        PlannedAction("models", "ensure", MEDIA_POLICY_NAME),
        PlannedAction("ontology", "ensure", ONTOLOGY_CODE),
    ]
    actions.extend(
        PlannedAction("documents", "upload-or-reuse", path.name)
        for path in discover_demo_documents(root)
    )
    actions.extend(
        [
            PlannedAction("sources", "seed", "国联演示本地目录与 MinIO 对象"),
            PlannedAction("sources", "ensure-and-sync", "Web / REST / RSS / Sitemap / Git / S3 / 本地目录"),
            PlannedAction("database", "ensure", "演示·国联经营数据 PostgreSQL"),
            PlannedAction("database", "ensure", "演示·国联经营数据 MySQL"),
            PlannedAction("database", "discover", "数据库 Schema"),
            PlannedAction("graph", "ensure", "演示业务实体、关系与来源证据"),
            PlannedAction("analysis", "ensure", "3 个 Semantica 规则推演任务"),
        ]
    )
    return actions


def find_by(rows: Sequence[Mapping[str, Any]], key: str, value: Any) -> dict[str, Any] | None:
    return next((dict(row) for row in rows if row.get(key) == value), None)


def _iter_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict):
        for key in ("items", "objects"):
            if isinstance(payload.get(key), list):
                return [dict(item) for item in payload[key]]
    return []


def paginated_items(
    api: ApiLike,
    path: str,
    *,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Load a complete offset/limit API collection without first-page bias."""

    if page_size < 1 or page_size > 500:
        raise ValueError("page_size 必须在 1 到 500 之间")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    separator = "&" if "?" in path else "?"
    while True:
        payload = api.call(
            "GET", f"{path}{separator}offset={offset}&limit={page_size}"
        )
        page = _iter_items(payload)
        for row in page:
            identity = str(row.get("id") or "")
            if identity and identity in seen_ids:
                continue
            if identity:
                seen_ids.add(identity)
            items.append(row)
        if len(page) < page_size:
            break
        offset += len(page)
    return items


def normalized_entity_name(value: Any) -> str:
    """Use the same Unicode/case normalization as the entity uniqueness key."""

    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()[:500]


def normalized_evidence_text(value: Any) -> str:
    """Normalize harmless parser punctuation differences for evidence matching."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def seeded_graph_ground_truth(root: Path = DEMO_ROOT) -> list[dict[str, Any]]:
    """Load fail-closed graph seed declarations from the checked-in Ground Truth."""

    path = root / "demo_ground_truth.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DemoError("无法读取演示图谱 Ground Truth") from exc
    rows: list[dict[str, Any]] = []
    triples: set[tuple[str, str, str]] = set()
    for fact in payload.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("seeded_graph") is not True:
            continue
        fact_id = str(fact.get("fact_id") or "").strip()
        relations = fact.get("expected_relations") or []
        source = fact.get("source") or {}
        evidence_terms = fact.get("evidence_terms") or []
        if (
            not fact_id
            or len(relations) != 1
            or not isinstance(relations[0], dict)
            or not isinstance(source, dict)
            or not str(source.get("filename") or "").strip()
            or not str(source.get("chunk_structural_path") or "").strip()
            or not isinstance(evidence_terms, list)
            or not evidence_terms
            or any(not str(value or "").strip() for value in evidence_terms)
        ):
            raise DemoError(f"图谱 Ground Truth 声明不完整：{fact_id or 'unknown'}")
        relation = relations[0]
        triple = tuple(str(relation.get(key) or "").strip() for key in ("subject", "predicate", "object"))
        if not all(triple) or triple in triples:
            raise DemoError(f"图谱 Ground Truth 关系为空或重复：{fact_id}")
        normalized_terms = "".join(normalized_evidence_text(value) for value in evidence_terms)
        if any(normalized_evidence_text(value) not in normalized_terms for value in triple):
            raise DemoError(f"图谱 Ground Truth 证据词未覆盖关系：{fact_id}")
        triples.add(triple)
        rows.append(
            {
                "fact_id": fact_id,
                "subject": triple[0],
                "predicate": triple[1],
                "object": triple[2],
                "source_title": str(source["filename"]),
                "chunk_structural_path": str(source["chunk_structural_path"]),
                "evidence_terms": [str(value) for value in evidence_terms],
                "required_for_inference": fact.get("required_for_inference") is True,
            }
        )
    if not rows:
        raise DemoError("图谱 Ground Truth 没有声明任何 seeded_graph 事实")
    return rows


def graph_entity_matches(
    row: Mapping[str, Any],
    *,
    name: str,
    entity_type: str,
) -> bool:
    if row.get("entity_type") != entity_type:
        return False
    current = normalized_entity_name(
        row.get("normalized_name") or row.get("canonical_name")
    )
    return current == normalized_entity_name(name)


def _current_knowledge_status(api: ApiLike, document: Mapping[str, Any]) -> str | None:
    current_version_id = document.get("current_version_id")
    if not current_version_id:
        return None
    detail = api.call("GET", f"/documents/{document['id']}")
    version = next(
        (item for item in detail.get("versions") or [] if item.get("id") == current_version_id),
        None,
    )
    return ((version or {}).get("parse_summary") or {}).get("knowledge_status")


class DemoPreparer:
    """Idempotently construct the isolated demo through public APIs."""

    def __init__(
        self,
        api: ApiLike,
        *,
        fixture_root: Path = DEMO_ROOT,
        user_password: str | None = None,
        database_password: str | None = None,
        object_store_endpoint: str | None = None,
        object_store_access_key: str | None = None,
        object_store_secret: str | None = None,
        wait_timeout_seconds: int = 3600,
        poll_seconds: float = 1.5,
        reprocess_failed: bool = False,
    ) -> None:
        self.api = api
        self.fixture_root = fixture_root
        self.user_password = user_password
        self.database_password = database_password
        self.object_store_endpoint = object_store_endpoint
        self.object_store_access_key = object_store_access_key
        self.object_store_secret = object_store_secret
        self.wait_timeout_seconds = wait_timeout_seconds
        self.poll_seconds = poll_seconds
        self.reprocess_failed = reprocess_failed
        self.report = PrepareReport()
        if self.user_password is not None and len(self.user_password) < 10:
            raise DemoError("GUOLIAN_DEMO_USER_PASSWORD 至少需要 10 个字符")
        if self.user_password and self.user_password.casefold().startswith(("replace_", "your-")):
            raise DemoError("GUOLIAN_DEMO_USER_PASSWORD 仍是文档占位值，请从安全渠道提供临时密码")

    def ensure_space(self) -> dict[str, Any]:
        existing = find_by(self.api.call("GET", "/spaces"), "code", SPACE_CODE)
        payload = {
            "name": SPACE_NAME,
            "description": (
                "用于展示多源知识接入、自动与人工治理、语义建模、知识图谱、"
                "规则推演、数据库问答、多模态理解和知识服务开放。"
                f"{DEMO_NOTICE}"
            ),
            "enabled": True,
        }
        if existing:
            space = self.api.call("PUT", f"/spaces/{existing['id']}", json=payload)
            status = "updated"
        else:
            space = self.api.call("POST", "/spaces", json={"code": SPACE_CODE, **payload})
            status = "created"
        self.report.space_id = space["id"]
        self.report.add("space", "ensure", SPACE_CODE, status)
        return space

    def ensure_identity(self, space: Mapping[str, Any]) -> dict[str, Any]:
        roles = {row["code"]: row for row in self.api.call("GET", "/roles")}
        users = {row["username"]: row for row in self.api.call("GET", "/users")}
        ensured_roles: dict[str, dict[str, Any]] = {}
        for spec in ROLE_SPECS:
            payload = {
                "name": spec["name"],
                "permissions": spec["permissions"],
                "enabled": True,
            }
            role = roles.get(spec["code"])
            if role:
                role = self.api.call("PUT", f"/roles/{role['id']}", json=payload)
                status = "updated"
            else:
                role = self.api.call("POST", "/roles", json={"code": spec["code"], **payload})
                status = "created"
            ensured_roles[spec["code"]] = role
            self.report.add("identity", "ensure-role", spec["code"], status)
            self.api.call(
                "POST",
                f"/spaces/{space['id']}/grants",
                json={
                    "subject_type": "role",
                    "subject_id": role["id"],
                    "permission": spec["space_permission"],
                    "effect": "allow",
                },
            )

        ensured_users: dict[str, dict[str, Any]] = {}
        for spec in USER_SPECS:
            role = ensured_roles[spec["role_code"]]
            payload = {
                "display_name": spec["display_name"],
                "role_ids": [role["id"]],
                "enabled": True,
                "is_admin": spec["is_admin"],
            }
            user = users.get(spec["username"])
            if user:
                user = self.api.call("PUT", f"/users/{user['id']}", json=payload)
                status = "updated"
            elif self.user_password:
                user = self.api.call(
                    "POST", "/users",
                    json={"username": spec["username"], "password": self.user_password, **payload},
                )
                status = "created"
            else:
                self.report.warnings.append(
                    f"未设置 GUOLIAN_DEMO_USER_PASSWORD，未创建用户 {spec['username']}"
                )
                continue
            ensured_users[spec["username"]] = user
            self.report.add("identity", "ensure-user", spec["username"], status)
        return {"roles": ensured_roles, "users": ensured_users}

    def _model(self, rows: Sequence[Mapping[str, Any]], kind: str) -> dict[str, Any] | None:
        candidates = [
            dict(row) for row in rows
            if (
                row.get("model_kind") == kind
                and row.get("enabled") is True
                and row.get("last_test_status") == "success"
            )
        ]
        if not candidates:
            return None
        return next((row for row in candidates if row.get("is_default")), candidates[0])

    def ensure_model_readiness(self) -> dict[str, Any]:
        """Test and bind only genuinely reachable models used by the demo.

        A historic ``last_test_status=success`` is not sufficient for a live
        customer demo.  This method sends the platform's real minimal request,
        refreshes the resolved routes, and only then repairs stale default
        routes or the default extraction policy.  It never receives or returns
        a plaintext API key.
        """

        models = [dict(row) for row in self.api.call("GET", "/model-configs")]
        qwen_api_key = (os.getenv("GUOLIAN_DEMO_QWEN_API_KEY") or "").strip()
        if qwen_api_key.casefold().startswith(("replace_", "your-")):
            raise DemoError("GUOLIAN_DEMO_QWEN_API_KEY 仍是文档占位值")
        provisioned_candidate_ids: set[str] = set()
        if qwen_api_key:
            qwen_base_url = safe_public_url(
                (os.getenv("GUOLIAN_DEMO_QWEN_BASE_URL") or
                 "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
            )
            qwen_llm = next(
                (
                    row for row in models
                    if row.get("model_kind") == "llm"
                    and str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
                ),
                None,
            )
            llm_payload = {
                "name": "阿里云千问 Qwen3.5 Plus",
                "model_kind": "llm",
                "provider": "openai_compatible",
                "model_name": "qwen3.5-plus",
                "base_url": qwen_base_url,
                "api_key": qwen_api_key,
                "config": {
                    "timeout": 120,
                    "retry": 2,
                    "concurrency": 4,
                    "temperature": 0,
                    "max_tokens": 8192,
                    "parameters": {"enable_thinking": False},
                },
                "enabled": True,
                "is_default": False,
            }
            if qwen_llm is None:
                qwen_llm = self.api.call(
                    "POST",
                    "/model-configs",
                    json=llm_payload,
                    _redact_secrets=(qwen_api_key,),
                )
                provisioned_candidate_ids.add(str(qwen_llm["id"]))
                qwen_action = "created"
            elif (
                qwen_llm.get("enabled") is not True
                or qwen_llm.get("last_test_status") == "failed"
            ):
                # A candidate created by an earlier failed clean-install run
                # must be recoverable. The explicit one-shot Secret may repair
                # only a disabled/known-failed record; a working or merely
                # untested existing credential is never rotated implicitly.
                qwen_llm = self.api.call(
                    "PUT",
                    f"/model-configs/{qwen_llm['id']}",
                    json=llm_payload,
                    _redact_secrets=(qwen_api_key,),
                )
                provisioned_candidate_ids.add(str(qwen_llm["id"]))
                qwen_action = "reconfigured"
            else:
                # Never rotate an existing credential implicitly. A mistyped
                # one-shot environment value must not break a working model;
                # operators rotate existing credentials through configuration
                # CRUD, where the action is explicit and audited.
                qwen_action = "existing"
            self.report.add(
                "models", "configure-from-secret", "阿里云千问 Qwen3.5 Plus", qwen_action,
            )

            models = [dict(row) for row in self.api.call("GET", "/model-configs")]
            qwen_vision = next(
                (
                    row for row in models
                    if row.get("model_kind") == "vision"
                    and str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
                ),
                None,
            )
            vision_payload = {
                "name": "阿里云千问 Qwen3.5 Plus 视觉理解",
                "model_kind": "vision",
                "provider": "openai_compatible",
                "model_name": "qwen3.5-plus",
                "base_url": qwen_base_url,
                "config": {
                    "credential_model_config_id": qwen_llm["id"],
                    "timeout": 180,
                    "retry": 2,
                    "max_tokens": 2048,
                    "parameters": {"enable_thinking": False},
                },
                "enabled": True,
                "is_default": False,
            }
            if qwen_vision is None:
                qwen_vision = self.api.call(
                    "POST",
                    "/model-configs",
                    json=vision_payload,
                    _redact_secrets=(qwen_api_key,),
                )
                provisioned_candidate_ids.add(str(qwen_vision["id"]))
                vision_action = "created"
            elif (
                qwen_vision.get("enabled") is not True
                or qwen_vision.get("last_test_status") == "failed"
            ):
                qwen_vision = self.api.call(
                    "PUT",
                    f"/model-configs/{qwen_vision['id']}",
                    json=vision_payload,
                    _redact_secrets=(qwen_api_key,),
                )
                provisioned_candidate_ids.add(str(qwen_vision["id"]))
                vision_action = "reconfigured"
            else:
                vision_action = "existing"
            self.report.add(
                "models", "configure-from-secret",
                "阿里云千问 Qwen3.5 Plus 视觉理解", vision_action,
            )
            # The plaintext credential only lives in this method and in the
            # authenticated request body. API responses, reports and logs use
            # the encrypted model record and never expose it.
            qwen_api_key = ""
            models = [dict(row) for row in self.api.call("GET", "/model-configs")]
        by_id = {row["id"]: row for row in models}
        resolved_payload = self.api.call("GET", "/model-routing-policies/resolved")
        resolved_rows = [dict(row) for row in resolved_payload.get("routes") or []]

        preferred_llm = next(
            (
                row for row in models
                if row.get("enabled") is True
                and row.get("model_kind") == "llm"
                and str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
            ),
            None,
        )
        candidate_ids: list[str] = []
        if preferred_llm:
            candidate_ids.append(preferred_llm["id"])
        candidate_ids.extend(
            str(row["id"])
            for row in models
            if str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
            and row.get("model_kind") in {"llm", "vision"}
            and row.get("enabled") is True
        )
        for route in resolved_rows:
            model_id = route.get("model_config_id")
            if model_id:
                candidate_ids.append(str(model_id))
        for kind in ("embedding", "vision", "asr"):
            default = next(
                (
                    row for row in models
                    if row.get("model_kind") == kind
                    and row.get("enabled") is True
                    and row.get("is_default") is True
                ),
                None,
            )
            if default:
                candidate_ids.append(default["id"])
        # Kimi is a documented backup.  Test it for an honest readiness report,
        # but never route to it merely because the test was attempted.
        candidate_ids.extend(
            row["id"] for row in models
            if row.get("enabled") is True and "kimi" in str(row.get("provider") or "").casefold()
        )

        tested: dict[str, dict[str, Any]] = {}
        for model_id in dict.fromkeys(candidate_ids):
            row = by_id.get(model_id)
            if not row:
                continue
            result = self.api.call("POST", f"/model-configs/{model_id}/test")
            tested[model_id] = {
                "name": row.get("name"),
                "kind": row.get("model_kind"),
                "status": result.get("status"),
                "elapsed_ms": result.get("elapsed_ms"),
            }
            self.report.add(
                "models", "live-test", str(row.get("name") or model_id),
                "succeeded" if result.get("status") == "success" else "failed",
                model_kind=row.get("model_kind"), elapsed_ms=result.get("elapsed_ms"),
            )

        for model_id in provisioned_candidate_ids:
            if (tested.get(model_id) or {}).get("status") != "success":
                self.api.call("PUT", f"/model-configs/{model_id}", json={"enabled": False})
                self.report.add(
                    "models", "disable-failed-candidate", model_id, "disabled",
                )

        # A model only becomes the type default after its real connection test
        # succeeds. This prevents an invalid environment Secret from replacing
        # the currently working configuration.
        provisioned_qwen = next(
            (
                row for row in models
                if row.get("model_kind") == "llm"
                and str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
            ),
            None,
        )
        if (
            provisioned_qwen
            and (tested.get(str(provisioned_qwen.get("id"))) or {}).get("status") == "success"
            and not provisioned_qwen.get("is_default")
        ):
            self.api.call(
                "PUT", f"/model-configs/{provisioned_qwen['id']}", json={"is_default": True},
            )
        provisioned_vision = next(
            (
                row for row in models
                if row.get("model_kind") == "vision"
                and str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
            ),
            None,
        )
        if (
            provisioned_vision
            and (tested.get(str(provisioned_vision.get("id"))) or {}).get("status") == "success"
            and not provisioned_vision.get("is_default")
        ):
            self.api.call(
                "PUT", f"/model-configs/{provisioned_vision['id']}", json={"is_default": True},
            )

        models = [dict(row) for row in self.api.call("GET", "/model-configs")]
        by_id = {row["id"]: row for row in models}
        qwen = next(
            (
                row for row in models
                if row.get("enabled") is True
                and row.get("model_kind") == "llm"
                and str(row.get("model_name") or "").casefold() == "qwen3.5-plus"
                and row.get("last_test_status") == "success"
            ),
            None,
        )
        successful_defaults = {
            kind: self._model(models, kind)
            for kind in ("embedding", "vision", "asr")
        }

        policies = [dict(row) for row in self.api.call("GET", "/model-routing-policies")]
        default_policy = next(
            (row for row in policies if row.get("enabled") is True and row.get("is_default") is True),
            None,
        )
        if qwen:
            routes = dict((default_policy or {}).get("routes") or {})
            for scene in DEMO_LLM_SCENES:
                routes[scene] = qwen["id"]
            for scene, kind in (
                ("embedding", "embedding"),
                ("vision_understanding", "vision"),
                ("speech_recognition", "asr"),
            ):
                model = successful_defaults[kind]
                routes[scene] = model["id"] if model else None
            if default_policy:
                default_policy = self.api.call(
                    "PUT", f"/model-routing-policies/{default_policy['id']}",
                    json={"description": DEMO_ROUTING_DESCRIPTION, "routes": routes},
                )
                routing_action = "updated"
            else:
                reusable_policy = next(
                    (row for row in policies if row.get("name") == DEMO_ROUTING_POLICY_NAME),
                    None,
                )
                if reusable_policy:
                    default_policy = self.api.call(
                        "PUT", f"/model-routing-policies/{reusable_policy['id']}",
                        json={
                            "description": DEMO_ROUTING_DESCRIPTION,
                            "routes": routes,
                            "enabled": True,
                            "is_default": True,
                        },
                    )
                    routing_action = "activated"
                else:
                    default_policy = self.api.call(
                        "POST", "/model-routing-policies",
                        json={
                            "name": DEMO_ROUTING_POLICY_NAME,
                            "description": DEMO_ROUTING_DESCRIPTION,
                            "routes": routes,
                            "enabled": True,
                            "is_default": True,
                        },
                    )
                    routing_action = "created"
            self.report.add(
                "models", "bind-routing-policy", str(default_policy.get("name")), routing_action,
            )
        elif not qwen:
            self.report.warnings.append("在线千问 Qwen3.5 Plus 未通过实时测试，未修改默认模型路由")

        # The default extraction policy is an explicit override and therefore
        # wins over the routing policy.  Repair it when it points at an absent,
        # disabled or currently failing model.
        extraction_policies = [
            dict(row) for row in self.api.call("GET", "/extraction-policies")
        ]
        default_extraction = next(
            (
                row for row in extraction_policies
                if row.get("enabled") is True and row.get("is_default") is True
            ),
            None,
        )
        if qwen and default_extraction:
            bound = by_id.get(str(default_extraction.get("model_config_id") or ""))
            if not bound or str(bound.get("id")) != str(qwen["id"]):
                self.api.call(
                    "PUT", f"/extraction-policies/{default_extraction['id']}",
                    json={"model_config_id": qwen["id"]},
                )
                self.report.add(
                    "models", "repair-extraction-policy",
                    str(default_extraction.get("name")), "updated",
                )

        refreshed = self.api.call("GET", "/model-routing-policies/resolved")
        routes = [dict(row) for row in refreshed.get("routes") or []]
        for route in routes:
            model = by_id.get(str(route.get("model_config_id") or ""))
            if route.get("scene") == "reranking" and not model:
                self.report.warnings.append("未配置可用重排模型，检索将真实降级且不伪造重排结果")
                continue
            if model and model.get("last_test_status") != "success":
                self.report.warnings.append(
                    f"场景“{route.get('label')}”当前模型未通过实时测试"
                )

        kimi = [
            item for item in tested.values()
            if "kimi" in str(item.get("name") or "").casefold()
        ]
        if kimi and not any(item.get("status") == "success" for item in kimi):
            self.report.warnings.append("Kimi 备用模型当前未通过真实连接测试，不参与演示路由")
        self.report.resources["model_preflight"] = {
            "tested": len(tested),
            "successful": sum(item.get("status") == "success" for item in tested.values()),
            "qwen_ready": bool(qwen),
            "embedding_ready": bool(successful_defaults["embedding"]),
            "vision_ready": bool(successful_defaults["vision"]),
            "asr_ready": bool(successful_defaults["asr"]),
            "reranker_ready": any(
                row.get("model_kind") == "reranker"
                and row.get("enabled") is True
                and row.get("last_test_status") == "success"
                for row in models
            ),
        }
        return {"models": models, "routes": routes, "tested": tested}

    def ensure_media_policy(self, space: Mapping[str, Any]) -> dict[str, Any]:
        models = self.api.call("GET", "/model-configs")
        asr = next(
            (
                dict(row) for row in models
                if row.get("model_kind") == "asr" and row.get("enabled") is True
                and row.get("is_default") is True
            ),
            None,
        )
        vision = next(
            (
                dict(row) for row in models
                if row.get("model_kind") == "vision" and row.get("enabled") is True
                and row.get("is_default") is True
            ),
            None,
        )
        asr_ready = bool(asr and asr.get("last_test_status") == "success")
        vision_ready = bool(vision and vision.get("last_test_status") == "success")
        config = {
            "processing_mode": "hybrid",
            "cloud_processing_allowed": vision_ready,
            "cloud_confirmation_mode": "per_upload" if vision_ready else "disabled",
            "failure_mode": "partial",
            "concurrency": 1,
            "video": {
                "extract_audio_track": True,
                "asr_enabled": asr_ready,
                "scene_detection_enabled": True,
            },
            "frame": {
                "mode": "fixed_interval",
                "interval_seconds": 10,
                "include_first": True,
                "include_last": True,
                "max_frames": 12,
                "max_image_edge": 1280,
            },
            "ocr": {"enabled": True, "language": "chi_sim+eng", "minimum_confidence": 30},
            "asr": {
                "enabled": asr_ready,
                "model_config_id": asr["id"] if asr_ready else None,
                "language": "zh",
                "minimum_speech_seconds": 0.2,
                "silence_policy": "metadata_only",
            },
            "vision": {
                "enabled": vision_ready,
                "model_config_id": vision["id"] if vision_ready else None,
                "execution": "cloud",
                "batch_size": 1,
                "concurrency": 1,
                "timeout_seconds": 180,
                "max_tokens": 1200,
                "prompt_version": "media-visible-facts-v1",
            },
        }
        policies = self.api.call("GET", "/media-policies")
        policy = find_by(policies, "name", MEDIA_POLICY_NAME)
        payload = {
            "name": MEDIA_POLICY_NAME,
            "description": f"每 10 秒抽帧、中文 OCR、真实 ASR 和视觉理解。{DEMO_NOTICE}",
            "applicable_media_types": ["image", "audio", "video"],
            "config": config,
            "enabled": True,
            "is_default": False,
        }
        if policy:
            policy = self.api.call("PUT", f"/media-policies/{policy['id']}", json=payload)
            status = "updated"
        else:
            policy = self.api.call("POST", "/media-policies", json=payload)
            status = "created"
        validation = self.api.call("POST", f"/media-policies/{policy['id']}/validate")
        if not validation.get("ok"):
            self.report.warnings.append("演示多模态策略尚未满足全部执行条件")
        if not asr_ready:
            self.report.warnings.append("ASR 模型未通过本轮真实测试，音视频转写不会被标记为已就绪")
        if not vision_ready:
            self.report.warnings.append("视觉模型未通过本轮真实测试，视觉描述不会被标记为已就绪")
        self.api.call("PUT", f"/spaces/{space['id']}", json={"media_policy_id": policy["id"]})
        self.report.add(
            "models", "ensure-media-policy", MEDIA_POLICY_NAME, status,
            asr_ready=asr_ready, vision_ready=vision_ready, policy_valid=bool(validation.get("ok")),
        )
        self.report.resources["media_policy_id"] = policy["id"]
        return policy

    def ensure_ontology(self, space: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        ontology = find_by(self.api.call("GET", f"/ontologies?space_id={space['id']}"), "code", ONTOLOGY_CODE)
        payload = {
            "space_id": space["id"],
            "name": "国联集团组织级知识底座演示本体",
            "namespace": "urn:chuanshen:demo:guolian-enterprise",
            "description": f"制度、组织、供应商、产品、项目、系统和采购业务语义。{DEMO_NOTICE}",
            "config": {"dataset": SPACE_CODE, "synthetic": True},
            "enabled": True,
        }
        if ontology:
            ontology = self.api.call("PUT", f"/ontologies/{ontology['id']}", json=payload)
            status = "updated"
        else:
            ontology = self.api.call("POST", "/ontologies", json={"code": ONTOLOGY_CODE, **payload})
            status = "created"
        terms = {row["code"]: row for row in self.api.call("GET", f"/ontologies/{ontology['id']}/terms")}
        for code, label, term_type in ONTOLOGY_TERMS:
            term_payload = {
                "label": label,
                "term_type": term_type,
                "definition": f"演示业务语义：{label}",
                "aliases": [],
                "constraints": {"dataset": SPACE_CODE},
                "enabled": True,
            }
            if code in terms:
                terms[code] = self.api.call("PUT", f"/ontology-terms/{terms[code]['id']}", json=term_payload)
            else:
                terms[code] = self.api.call(
                    "POST", f"/ontologies/{ontology['id']}/terms", json={"code": code, **term_payload}
                )
        self.report.add("ontology", "ensure", ONTOLOGY_CODE, status, terms=len(terms))
        self.report.resources["ontology_id"] = ontology["id"]
        return ontology, terms

    def wait_job(self, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.api.call("GET", f"/jobs/{job_id}")
            status = last.get("status")
            if status in {"succeeded", "failed", "cancelled"}:
                if status != "succeeded":
                    code = str(last.get("error_code") or "UNKNOWN")
                    message = redact(str(last.get("error_message") or ""))[:300]
                    suffix = f"（{code}{'：' + message if message else ''}）"
                    raise DemoError(f"任务 {job_id} 未成功：{status}{suffix}")
                return last
            time.sleep(self.poll_seconds)
        raise DemoError(f"任务 {job_id} 在 {self.wait_timeout_seconds} 秒内未完成")

    def wait_knowledge_job(self, version_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            for job in self.api.call("GET", "/jobs"):
                if (
                    job.get("job_type") == "process_knowledge"
                    and (job.get("input") or {}).get("version_id") == version_id
                ):
                    return self.wait_job(job["id"])
            time.sleep(self.poll_seconds)
        raise DemoError(f"文档版本 {version_id} 未创建知识加工任务")

    def _reprocess_document(
        self,
        document_id: str,
        *,
        attempts: int = 3,
        processing_mode: str = "both",
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                job = self.api.call(
                    "POST",
                    f"/documents/{document_id}/process?force=true",
                    json={"mode": processing_mode},
                )
                return self.wait_job(job["id"])
            except DemoError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(min(2 * attempt, 5))
        assert last_error is not None
        raise last_error

    def _upload_demo_document_version(
        self,
        *,
        path: Path,
        content: bytes,
        space_id: str,
        media_policy: Mapping[str, Any],
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload and fully process one allowlisted demo file or new version."""

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data: dict[str, Any] = {
            "space_id": space_id,
            "knowledge_processing_mode": "both",
        }
        if document_id:
            data["document_id"] = document_id
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".wav", ".mp3", ".mp4", ".mov"}:
            data.update(
                {
                    "media_policy_id": media_policy["id"],
                    "cloud_processing_confirmed": "true",
                    "frame_budget_confirmed": "true",
                }
            )
        uploaded = self.api.call(
            "POST",
            "/documents/upload",
            data=data,
            files={"file": (path.name, content, content_type)},
        )
        self.wait_job(uploaded["job"]["id"])
        try:
            self.wait_knowledge_job(uploaded["version"]["id"])
        except DemoError:
            # Model gateways can have transient read timeouts. Retry the
            # platform task; never replace it with generated knowledge.
            self._reprocess_document(uploaded["document"]["id"], attempts=2)
        self.api.call(
            "PUT",
            f"/documents/{uploaded['document']['id']}",
            json={"tags": ["国联集团演示", "组织级知识底座", "演示数据"]},
        )
        return uploaded

    def ensure_documents(self, space: Mapping[str, Any], media_policy: Mapping[str, Any]) -> list[dict[str, Any]]:
        missing = missing_required_demo_documents(self.fixture_root)
        if missing:
            raise DemoError("主演示素材不完整：" + "、".join(missing))
        existing = {
            row["title"]: row
            for row in self.api.call("GET", f"/documents?space_id={space['id']}")
        }
        outcomes: list[dict[str, Any]] = []
        failures: list[str] = []
        for path in discover_demo_documents(self.fixture_root):
            content = path.read_bytes()
            local_sha256 = hashlib.sha256(content).hexdigest()
            row = existing.get(path.name)
            if row:
                detail = self.api.call("GET", f"/documents/{row['id']}")
                current_version_id = detail.get("current_version_id") or row.get("current_version_id")
                current_version = next(
                    (
                        version
                        for version in detail.get("versions") or []
                        if version.get("id") == current_version_id
                    ),
                    None,
                )
                current_sha256 = str((current_version or {}).get("sha256") or "").lower()
                if current_sha256 and current_sha256 != local_sha256:
                    try:
                        uploaded = self._upload_demo_document_version(
                            path=path,
                            content=content,
                            space_id=str(space["id"]),
                            media_policy=media_policy,
                            document_id=str(row["id"]),
                        )
                        outcome = {
                            "title": path.name,
                            "status": "new-version",
                            "document_id": uploaded["document"]["id"],
                            "version_id": uploaded["version"]["id"],
                        }
                        outcomes.append(outcome)
                        self.report.add(
                            "documents",
                            "upload-new-version",
                            path.name,
                            "succeeded",
                        )
                    except DemoError as exc:
                        failures.append(f"{path.name}：{exc}")
                        outcomes.append(
                            {"title": path.name, "status": "failed", "document_id": row["id"]}
                        )
                        self.report.add(
                            "documents", "upload-new-version", path.name, "failed"
                        )
                    continue
                knowledge_status = _current_knowledge_status(self.api, row)
                if knowledge_status == "published":
                    outcome = {"title": path.name, "status": "existing", "document_id": row["id"]}
                    outcomes.append(outcome)
                    self.report.add("documents", "upload-or-reuse", path.name, "existing")
                    continue
                if self.reprocess_failed:
                    try:
                        self._reprocess_document(row["id"])
                        outcome = {"title": path.name, "status": "reprocessed", "document_id": row["id"]}
                        outcomes.append(outcome)
                        self.report.add("documents", "reprocess", path.name, "succeeded")
                    except DemoError as exc:
                        failures.append(f"{path.name}：{exc}")
                        outcomes.append({"title": path.name, "status": "failed", "document_id": row["id"]})
                        self.report.add("documents", "reprocess", path.name, "failed")
                    continue
                self.report.warnings.append(f"文档 {path.name} 已存在但知识加工状态为 {knowledge_status or 'unknown'}")
                outcomes.append({"title": path.name, "status": "existing-incomplete", "document_id": row["id"]})
                continue

            try:
                uploaded = self._upload_demo_document_version(
                    path=path,
                    content=content,
                    space_id=str(space["id"]),
                    media_policy=media_policy,
                )
                outcome = {
                    "title": path.name,
                    "status": "uploaded",
                    "document_id": uploaded["document"]["id"],
                    "version_id": uploaded["version"]["id"],
                }
                outcomes.append(outcome)
                self.report.add("documents", "upload", path.name, "succeeded")
            except DemoError as exc:
                failures.append(f"{path.name}：{exc}")
                outcomes.append({"title": path.name, "status": "failed"})
                self.report.add("documents", "upload", path.name, "failed")
        self.report.resources["documents"] = len(outcomes)
        if failures:
            raise DemoError(
                f"{len(failures)} 个演示文档未完成真实加工：" + "；".join(failures)
            )
        return outcomes

    def _source_fixture_files(self, names: Sequence[str]) -> list[tuple[str, bytes, str]]:
        files: list[tuple[str, bytes, str]] = []
        for name in names:
            path = self.fixture_root / name
            if not path.is_file():
                raise DemoError(f"多源演示素材不存在：{name}")
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            files.append((name, path.read_bytes(), content_type))
        return files

    def seed_account_free_source_fixtures(self) -> dict[str, Any]:
        """Idempotently seed only the dedicated local-directory and MinIO fixtures.

        This is fixture provisioning rather than business-data access.  The
        worker still reads every byte through the real Semantica connector
        adapter during connection tests and synchronization.
        """

        if not (
            self.object_store_endpoint
            and self.object_store_access_key
            and self.object_store_secret
        ):
            raise DemoError(
                "sources 阶段需要 GUOLIAN_DEMO_MINIO_ENDPOINT、"
                "GUOLIAN_DEMO_MINIO_ACCESS_KEY 和 GUOLIAN_DEMO_MINIO_SECRET"
            )

        local_root = Path(
            os.getenv("GUOLIAN_DEMO_LOCAL_SOURCE_ROOT", SOURCE_FIXTURE_LOCAL_ROOT)
        ).resolve()
        configured_mount_root = Path(
            os.getenv("GUOLIAN_DEMO_SOURCE_MOUNT_ROOT", "/app/data/sources")
        ).resolve()
        if local_root == configured_mount_root or not local_root.is_relative_to(configured_mount_root):
            raise DemoError("国联演示本地目录必须位于专用数据源挂载根目录的子目录中")
        local_root.mkdir(parents=True, exist_ok=True)
        local_files = self._source_fixture_files(LOCAL_SOURCE_FIXTURE_FILES)
        for name, body, _content_type in local_files:
            (local_root / name).write_bytes(body)

        endpoint = str(self.object_store_endpoint).strip()
        if "://" in endpoint or "/" in endpoint or "@" in endpoint:
            raise DemoError("MinIO Endpoint 必须使用不含凭据和路径的 host:port")
        from minio import Minio

        minio = Minio(
            endpoint,
            access_key=str(self.object_store_access_key),
            secret_key=str(self.object_store_secret),
            secure=False,
        )
        if not minio.bucket_exists(SOURCE_FIXTURE_BUCKET):
            minio.make_bucket(SOURCE_FIXTURE_BUCKET)
        object_files = self._source_fixture_files(OBJECT_SOURCE_FIXTURE_FILES)
        for name, body, content_type in object_files:
            minio.put_object(
                SOURCE_FIXTURE_BUCKET,
                f"{SOURCE_FIXTURE_PREFIX}{name}",
                BytesIO(body),
                len(body),
                content_type=content_type,
            )
        seeded = {"local_files": len(local_files), "object_files": len(object_files)}
        self.report.add(
            "sources", "seed-fixtures", "专用本地目录与 MinIO Bucket", "succeeded", **seeded,
        )
        return {**seeded, "local_root": str(local_root)}

    def _account_free_source_definitions(self, *, local_root: str) -> tuple[dict[str, Any], ...]:
        base = safe_public_url(
            os.getenv("GUOLIAN_DEMO_SOURCE_FIXTURE_URL", SOURCE_FIXTURE_BASE_URL)
        )
        git_url = os.getenv("GUOLIAN_DEMO_GIT_SOURCE_URL", SOURCE_FIXTURE_GIT_URL).strip()
        parsed_git = urlsplit(git_url)
        if parsed_git.scheme not in {"http", "https", "git"} or not parsed_git.hostname:
            raise DemoError("Git 演示源地址必须是有效的 HTTP、HTTPS 或 Git URL")
        if parsed_git.username or parsed_git.password:
            raise DemoError("Git 演示源地址不能包含凭据")
        assert self.object_store_endpoint and self.object_store_access_key and self.object_store_secret
        common = {"schedule_minutes": 0}
        return (
            {
                "name": "演示·集团项目运行门户", "source_type": "web",
                "config": {**common, "url": f"{base}/guolian/portal", "respect_robots": True, "timeout": 30},
            },
            {
                "name": "演示·供应商风险 REST API", "source_type": "rest",
                "config": {**common, "url": f"{base}/guolian/api/risks", "method": "GET", "response_mode": "json", "timeout": 30},
            },
            {
                "name": "演示·采购与风险动态 RSS", "source_type": "rss",
                "config": {**common, "url": f"{base}/guolian/feed.xml", "max_items": 10, "timeout": 30},
            },
            {
                "name": "演示·集团知识站点 Sitemap", "source_type": "sitemap",
                "config": {**common, "url": f"{base}/guolian/sitemap.xml", "max_urls": 10, "respect_robots": True, "timeout": 30},
            },
            {
                "name": "演示·项目配置 Git 仓库", "source_type": "git",
                "config": {**common, "url": git_url, "depth": 1, "include_extensions": ["md", "yaml", "yml", "json", "py"]},
            },
            {
                "name": "演示·MinIO 项目对象库", "source_type": "s3",
                "config": {
                    **common, "endpoint": self.object_store_endpoint,
                    "bucket": SOURCE_FIXTURE_BUCKET, "prefix": SOURCE_FIXTURE_PREFIX,
                    "access_key": self.object_store_access_key, "secure": False, "max_files": 20,
                },
                "secret": self.object_store_secret,
            },
            {
                "name": "演示·共享目录知识投递", "source_type": "local_dir",
                "config": {**common, "path": local_root, "recursive": True, "max_files": 20},
            },
        )

    def _wait_source_pipeline(self, sync_job: Mapping[str, Any]) -> dict[str, Any]:
        completed = self.wait_job(str(sync_job["id"]))
        result = dict(completed.get("result") or {})
        parse_job_id = result.get("parse_job_id")
        version_id = result.get("version_id")
        knowledge_error: DemoError | None = None
        if parse_job_id:
            self.wait_job(str(parse_job_id))
            if version_id:
                try:
                    self.wait_knowledge_job(str(version_id))
                except DemoError as exc:
                    # Do not accept a partial graph merely because the vector
                    # release was published.  Inspect the durable version
                    # state below and perform a bounded real retry.
                    knowledge_error = exc
        for child_job_id in result.get("media_parse_job_ids") or []:
            self.wait_job(str(child_job_id))

        document_id = result.get("document_id")
        if version_id and document_id:
            detail = self.api.call("GET", f"/documents/{document_id}")
            current_version_id = detail.get("current_version_id")
            if current_version_id != version_id:
                raise DemoError(
                    f"数据源同步版本 {version_id} 不是文档 {document_id} 的当前版本，"
                    "不能确认知识加工结果"
                )
            version = next(
                (
                    item
                    for item in detail.get("versions") or []
                    if item.get("id") == version_id
                ),
                None,
            )
            if version is None:
                raise DemoError(f"数据源同步版本 {version_id} 无法在文档详情中核验")
            summary = dict(version.get("parse_summary") or {})
            knowledge_status = summary.get("knowledge_status")
            if knowledge_status != "published":
                requested_mode = str(
                    summary.get("knowledge_processing_requested_mode")
                    or summary.get("knowledge_processing_mode")
                    or "both"
                )
                try:
                    self._reprocess_document(
                        str(document_id),
                        attempts=2,
                        processing_mode=requested_mode,
                    )
                except DemoError as exc:
                    original = f"；原任务：{knowledge_error}" if knowledge_error else ""
                    raise DemoError(
                        f"数据源文档 {document_id} 的知识加工状态为 "
                        f"{knowledge_status or 'unknown'}，两次真实重试后仍未完整发布"
                        f"{original}"
                    ) from exc
                refreshed = self.api.call("GET", f"/documents/{document_id}")
                refreshed_version = next(
                    (
                        item
                        for item in refreshed.get("versions") or []
                        if item.get("id") == version_id
                    ),
                    None,
                )
                final_status = (
                    ((refreshed_version or {}).get("parse_summary") or {}).get(
                        "knowledge_status"
                    )
                )
                if final_status != "published":
                    raise DemoError(
                        f"数据源文档 {document_id} 重试任务已结束，但版本 {version_id} "
                        f"仍为 {final_status or 'unknown'}"
                    )
                result["knowledge_reprocessed"] = True
                knowledge_status = final_status
            result["knowledge_status"] = knowledge_status
        elif knowledge_error:
            # A failed knowledge task without a resolvable durable target must
            # remain a hard preparation failure.
            raise knowledge_error
        return result

    def ensure_account_free_sources(self, space: Mapping[str, Any]) -> list[dict[str, Any]]:
        fixtures = self.seed_account_free_source_fixtures()
        existing = {
            row["name"]: row
            for row in self.api.call("GET", f"/sources?space_id={space['id']}")
        }
        outcomes: list[dict[str, Any]] = []
        for definition in self._account_free_source_definitions(local_root=fixtures["local_root"]):
            create_payload = {
                "space_id": space["id"], "enabled": True, **definition,
            }
            source = existing.get(definition["name"])
            if source:
                source = self.api.call(
                    "PUT", f"/sources/{source['id']}", json=create_payload,
                )
                create_status = "updated"
            else:
                source = self.api.call("POST", "/sources", json=create_payload)
                create_status = "created"

            tested = self.api.call(
                "POST", "/sources/test",
                json={
                    "source_id": source["id"],
                    "source_type": source["source_type"],
                    "config": source["config"],
                },
            )
            if tested.get("status") != "success" or int(tested.get("bytes") or 0) <= 0:
                raise DemoError(f"{source['name']} 真实连接测试未成功")
            sync_result = self._wait_source_pipeline(
                self.api.call("POST", f"/sources/{source['id']}/sync")
            )
            status = "unchanged" if sync_result.get("unchanged") else "synchronized"
            outcome = {
                "name": source["name"],
                "source_id": source["id"],
                "source_type": source["source_type"],
                "connection": "success",
                "sync": status,
                "document_id": sync_result.get("document_id"),
                "version_id": sync_result.get("version_id"),
            }
            outcomes.append(outcome)
            self.report.add(
                "sources", "ensure-test-sync", source["name"], status,
                source_type=source["source_type"], create_status=create_status,
            )

        documents = self.api.call("GET", f"/documents?space_id={space['id']}")
        by_source = {row["source_id"]: row for row in documents if row.get("source_id")}
        for outcome in outcomes:
            document = by_source.get(outcome["source_id"])
            if document:
                self.api.call(
                    "PUT", f"/documents/{document['id']}",
                    json={
                        "tags": [
                            "国联集团演示", "组织级知识底座", "数据源同步",
                            outcome["source_type"], "演示数据",
                        ]
                    },
                )
        self.report.resources["account_free_sources"] = outcomes
        return outcomes

    def _database_config(self, dialect: str) -> dict[str, Any]:
        if dialect == "postgresql":
            return {
                "dialect": "postgresql",
                "host": os.getenv("GUOLIAN_DEMO_POSTGRES_HOST", "guolian-demo-postgres"),
                "port": int(os.getenv("GUOLIAN_DEMO_POSTGRES_INTERNAL_PORT", "5432")),
                "database": os.getenv("GUOLIAN_DEMO_POSTGRES_DATABASE", "guolian_demo"),
                "username": os.getenv("GUOLIAN_DEMO_POSTGRES_USER", "guolian_demo"),
                "schema": os.getenv("GUOLIAN_DEMO_POSTGRES_SCHEMA", "public"),
                "include_tables": list(DATABASE_OBJECTS),
                "knowledge_index_enabled": True,
                "realtime_query_enabled": True,
                "graph_materialization_enabled": True,
                "generic_semantic_extraction_enabled": False,
                "database_profile_model_enabled": False,
            }
        return {
            "dialect": "mysql",
            "host": os.getenv("GUOLIAN_DEMO_MYSQL_HOST", "guolian-demo-mysql"),
            "port": int(os.getenv("GUOLIAN_DEMO_MYSQL_INTERNAL_PORT", "3306")),
            "database": os.getenv("GUOLIAN_DEMO_MYSQL_DATABASE", "guolian_demo"),
            "username": os.getenv("GUOLIAN_DEMO_MYSQL_USER", "guolian_demo"),
            "include_tables": list(DATABASE_OBJECTS),
            "knowledge_index_enabled": True,
            "realtime_query_enabled": True,
            "graph_materialization_enabled": False,
            "generic_semantic_extraction_enabled": False,
            "database_profile_model_enabled": False,
        }

    def ensure_database_sources(self, space: Mapping[str, Any]) -> dict[str, Any]:
        if not self.database_password:
            self.report.warnings.append(
                "未设置 GUOLIAN_DEMO_DATABASE_PASSWORD / STRUCTURED_FIXTURE_PASSWORD，数据库接入未执行"
            )
            return {}
        existing = {
            row["name"]: row
            for row in self.api.call("GET", f"/sources?space_id={space['id']}")
        }
        results: dict[str, Any] = {}
        for dialect, name in (
            ("postgresql", "演示·国联经营数据 PostgreSQL"),
            ("mysql", "演示·国联经营数据 MySQL"),
        ):
            config = self._database_config(dialect)
            source = existing.get(name)
            payload = {
                "space_id": space["id"], "name": name, "source_type": "database",
                "config": config, "enabled": True,
            }
            if source:
                source = self.api.call("PUT", f"/sources/{source['id']}", json=payload)
                status = "updated"
            else:
                source = self.api.call("POST", "/sources", json={**payload, "secret": self.database_password})
                status = "created"
            tested = self.api.call(
                "POST", "/sources/test",
                json={"source_id": source["id"], "source_type": "database", "config": config},
            )
            if tested.get("status") != "success":
                raise DemoError(f"{name} 连接测试未成功")
            schema = self.api.call("POST", f"/sources/{source['id']}/schema/discover")
            catalog = schema.get("catalog") or {}
            object_ids = {item.get("id") for item in catalog.get("objects") or []}
            prefix = "public." if dialect == "postgresql" else ""
            required = {f"{prefix}{name}" for name in ("suppliers", "products", "projects", "purchase_orders")}
            if not required <= object_ids:
                raise DemoError(f"{dialect} 演示 Schema 缺少必需业务表")
            results[dialect] = {
                "source_id": source["id"],
                "schema_version_id": schema["id"],
                "objects": len(catalog.get("objects") or []),
                "source": source,
                "schema": schema,
            }
            self.report.add("database", "ensure-source", name, status, schema_objects=results[dialect]["objects"])
        self.report.resources["database"] = {
            dialect: {
                "source_id": item["source_id"],
                "schema_version_id": item["schema_version_id"],
                "objects": item["objects"],
            }
            for dialect, item in results.items()
        }
        return results

    def _mapping_manifest(
        self,
        *,
        source_id: str,
        schema_id: str,
        ontology_id: str,
        terms: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        def oid(name: str) -> str:
            return f"public.{name}"

        def cid(table: str, column: str) -> str:
            return f"{oid(table)}.{column}"

        entity_specs = {
            "org-unit": ("organization", "org_units", "unit_name", "组织"),
            "supplier": ("supplier", "suppliers", "supplier_name", "供应商"),
            "product": ("product", "products", "product_name", "产品"),
            "project": ("project", "projects", "project_name", "项目"),
            "purchase-order": ("purchase_order", "purchase_orders", "order_no", "采购订单"),
            "purchase-item": ("purchase_order_item", "purchase_order_items", "id", "采购订单明细"),
            "approval-record": ("approval_record", "approval_records", "approval_stage", "审批记录"),
            "procurement-target": ("procurement_target", "procurement_targets", "id", "采购目标"),
            "risk-event": ("risk_event", "risk_events", "risk_type", "风险事件"),
            "project-product": ("project_product", "project_products", "usage_role", "项目产品关联"),
        }
        entities = [
            {
                "id": entity_id,
                "ontology_term_id": terms[term_code]["id"],
                "label": label,
                "description": f"{label}表中的每行形成稳定业务对象",
                "fragments": [
                    {
                        "id": f"{entity_id}-main",
                        "object_id": oid(table),
                        "role": "primary",
                        "identity_column_ids": (
                            [cid(table, "project_id"), cid(table, "product_id")]
                            if table == "project_products" else [cid(table, "id")]
                        ),
                        "display_column_id": cid(table, display_column),
                        "grain": label,
                    }
                ],
            }
            for entity_id, (term_code, table, display_column, label) in entity_specs.items()
        ]
        attribute_specs = (
            ("org-code", "code", "org-unit", "org_units", "unit_code", "组织编码", "string", False),
            ("org-name", "name", "org-unit", "org_units", "unit_name", "组织名称", "string", False),
            ("supplier-code", "code", "supplier", "suppliers", "supplier_code", "供应商编码", "string", False),
            ("supplier-name", "name", "supplier", "suppliers", "supplier_name", "供应商名称", "string", False),
            ("supplier-risk", "risk_level", "supplier", "suppliers", "risk_level", "供应商风险等级", "string", False),
            ("product-name", "name", "product", "products", "product_name", "产品名称", "string", False),
            ("project-name", "name", "project", "projects", "project_name", "项目名称", "string", False),
            ("order-no", "code", "purchase-order", "purchase_orders", "order_no", "订单编号", "string", False),
            ("order-date", "date", "purchase-order", "purchase_orders", "order_date", "采购日期", "date", False),
            ("order-executed-at", "date", "purchase-order", "purchase_orders", "executed_at", "执行时间", "datetime", False),
            ("order-status", "status", "purchase-order", "purchase_orders", "status", "订单状态", "string", False),
            ("order-amount", "amount", "purchase-order", "purchase_orders", "amount", "有效采购金额", "number", True),
            ("order-gross-amount", "amount", "purchase-order", "purchase_orders", "amount", "订单金额（仅排除已取消）", "number", True),
            ("approval-decision", "decision", "approval-record", "approval_records", "decision", "审批结论", "string", False),
            ("item-line-amount", "amount", "purchase-item", "purchase_order_items", "line_amount", "采购明细金额", "number", True),
            ("target-year", "date", "procurement-target", "procurement_targets", "target_year", "目标年份", "integer", False),
            ("target-amount", "amount", "procurement-target", "procurement_targets", "target_amount", "采购目标金额", "number", True),
            ("risk-type", "name", "risk-event", "risk_events", "risk_type", "风险类型", "string", False),
            ("risk-date", "date", "risk-event", "risk_events", "event_date", "风险日期", "date", False),
            ("risk-event-level", "risk_level", "risk-event", "risk_events", "risk_level", "风险事件等级", "string", False),
            ("risk-severity", "risk_level", "risk-event", "risk_events", "severity", "风险严重度", "integer", False),
            ("usage-role", "name", "project-product", "project_products", "usage_role", "产品使用角色", "string", False),
            ("project-product-active", "status", "project-product", "project_products", "active", "项目产品关联是否启用", "boolean", False),
        )
        attributes: list[dict[str, Any]] = []
        for attribute_id, term_code, entity_id, table, column, label, semantic_type, is_measure in attribute_specs:
            item: dict[str, Any] = {
                "id": attribute_id,
                "ontology_term_id": terms[term_code]["id"],
                "entity_id": entity_id,
                "fragment_id": f"{entity_id}-main",
                "column_id": cid(table, column),
                "label": label,
                "semantic_type": semantic_type,
                "is_measure": is_measure,
            }
            if attribute_id == "order-amount":
                item.update(
                    {
                        "aliases": ["制度有效采购金额", "审批通过采购金额", "有效采购金额"],
                        "business_definition": "有效采购金额仅统计 signed、executing、accepted 状态，且至少存在一条审批结论为 approved 的关联审批记录；取消订单和未审批通过订单不计入。",
                        "default_aggregate": "sum",
                        "required_filters": [
                            {
                                "attribute_id": "order-status",
                                "operator": "in",
                                "value": ["signed", "executing", "accepted"],
                            }
                        ],
                        "required_relationships": [
                            {
                                "relationship_id": "approval-order",
                                "target_entity_id": "approval-record",
                                "quantifier": "exists",
                                "filters": [
                                    {
                                        "attribute_id": "approval-decision",
                                        "operator": "eq",
                                        "value": "approved",
                                    }
                                ],
                                "description": "订单必须存在审批结论为 approved 的审批记录，使用 EXISTS 避免多条审批记录导致金额重复累加。",
                            }
                        ],
                    }
                )
            elif attribute_id == "order-gross-amount":
                item.update(
                    {
                        "aliases": ["只排除取消订单的金额", "未应用审批条件的订单金额", "订单金额"],
                        "business_definition": "订单金额仅排除 cancelled 状态，不应用审批通过条件；该指标用于核对制度口径应用前的订单头金额，不等同于有效采购金额。",
                        "default_aggregate": "sum",
                        "required_filters": [
                            {
                                "attribute_id": "order-status",
                                "operator": "ne",
                                "value": "cancelled",
                            }
                        ],
                        "required_relationships": [],
                    }
                )
            elif is_measure:
                item.update(
                    {
                        "aliases": [label],
                        "business_definition": f"演示数据中的{label}",
                        "default_aggregate": "sum",
                    }
                )
            elif attribute_id == "supplier-risk":
                item.update({
                    "aliases": ["供应商主数据风险等级", "供应商静态风险等级"],
                    "business_definition": "供应商主数据的静态风险等级，不代表某一条风险事件的等级。",
                })
            elif attribute_id == "risk-event-level":
                item.update({
                    "aliases": ["高风险事件", "风险事件风险等级", "事件等级"],
                    "business_definition": "单条风险事件记录的风险等级；问题明确提到高风险事件时使用。",
                })
            elif attribute_id == "order-executed-at":
                item.update({
                    "aliases": ["已执行时间", "实际执行时间", "执行日期"],
                    "business_definition": "订单真正进入执行后记录的时间；判断‘已经执行’时应同时要求该字段不为空。",
                })
            attributes.append(item)

        relation_specs = (
            ("order-org", "belongs_to", "purchase-order", "org-unit", "purchase_orders", "org_unit_id", "org_units", "id", "订单所属组织"),
            ("order-supplier", "belongs_to", "purchase-order", "supplier", "purchase_orders", "supplier_id", "suppliers", "id", "订单供应商"),
            ("order-project", "correspond_to", "purchase-order", "project", "purchase_orders", "project_id", "projects", "id", "订单对应项目"),
            ("item-order", "belongs_to", "purchase-item", "purchase-order", "purchase_order_items", "purchase_order_id", "purchase_orders", "id", "明细所属订单"),
            ("approval-order", "correspond_to", "approval-record", "purchase-order", "approval_records", "purchase_order_id", "purchase_orders", "id", "审批记录对应订单"),
            ("item-product", "correspond_to", "purchase-item", "product", "purchase_order_items", "product_id", "products", "id", "明细对应产品"),
            ("target-org", "belongs_to", "procurement-target", "org-unit", "procurement_targets", "org_unit_id", "org_units", "id", "采购目标所属组织"),
            ("risk-product", "involve", "risk-event", "product", "risk_events", "affected_product_id", "products", "id", "风险影响产品"),
            ("risk-supplier", "involve", "risk-event", "supplier", "risk_events", "supplier_id", "suppliers", "id", "风险涉及供应商"),
            ("project-product-project", "correspond_to", "project-product", "project", "project_products", "project_id", "projects", "id", "关联记录对应项目"),
            ("project-product-product", "correspond_to", "project-product", "product", "project_products", "product_id", "products", "id", "关联记录对应产品"),
        )
        relationships = [
            {
                "id": relation_id,
                "ontology_term_id": terms[term_code]["id"],
                "label": label,
                "from_entity_id": from_entity,
                "to_entity_id": to_entity,
                "cardinality": "many_to_one",
                "predicates": [
                    {
                        "left": {"object_id": oid(left_table), "column_id": cid(left_table, left_column)},
                        "operator": "=",
                        "right": {"object_id": oid(right_table), "column_id": cid(right_table, right_column)},
                    }
                ],
            }
            for (
                relation_id, term_code, from_entity, to_entity,
                left_table, left_column, right_table, right_column, label,
            ) in relation_specs
        ]
        relationship_descriptions = {
            "risk-supplier": "标识风险事件的相关供应商，仅用于筛选风险主体；不能据此把该供应商的全部订单或项目视为受该风险影响。",
            "risk-product": "标识风险事件记录中明确受影响的产品；查询风险影响项目时，应从该产品继续沿项目产品关联定位项目。",
            "project-product-product": "项目产品关联记录对应的产品，用于从受影响产品定位采用该产品的项目关联记录。",
            "project-product-project": "项目产品关联记录对应的项目，用于得到实际采用相关产品的项目。",
        }
        for relationship in relationships:
            relationship["description"] = relationship_descriptions.get(
                relationship["id"], f"{relationship['label']}的确定性主外键关系"
            )
            if relationship["id"] in {"project-product-product", "project-product-project"}:
                relationship["required_filters"] = [{
                    "attribute_id": "project-product-active",
                    "operator": "eq",
                    "value": True,
                }]
        derived_metrics = [{
            "id": "procurement-target-completion-rate",
            "label": "采购目标完成率",
            "aliases": ["目标完成率", "采购计划完成率"],
            "numerator_attribute_id": "order-amount",
            "denominator_attribute_id": "target-amount",
            "numerator_display_label": "有效采购金额",
            "denominator_display_label": "采购目标",
            "scale": 100,
            "numerator_time_attribute_id": "order-date",
            "denominator_period_attribute_id": "target-year",
            "default_period": 2026,
            "dimensions": [{
                "entity_id": "org-unit",
                "attribute_id": "org-name",
                "numerator_relationship_id": "order-org",
                "denominator_relationship_id": "target-org",
                "label": "组织单位",
                "aliases": ["单位", "组织", "各单位"],
            }],
            "description": "采购目标完成率按有效采购金额除以同期采购目标金额并乘以 100%，分母为零时返回空值。",
        }]
        record_sets = [{
            "id": "executed-with-pending-approval-orders",
            "label": "已执行但审批尚未完成的订单",
            "aliases": ["已经执行但审批尚未完成", "已执行但审批待补充", "先执行后审批"],
            "base_entity_id": "purchase-order",
            "identity_attribute_id": "order-no",
            "filters": [
                {"attribute_id": "order-status", "operator": "eq", "value": "executing"},
                {"attribute_id": "order-executed-at", "operator": "is_not_null"},
            ],
            "relationship_constraints": [{
                "relationship_id": "approval-order",
                "target_entity_id": "approval-record",
                "quantifier": "exists",
                "filters": [{"attribute_id": "approval-decision", "operator": "eq", "value": "pending"}],
                "description": "订单必须存在审批结论为 pending 的审批记录。",
            }],
            "description": "统计已经进入执行状态、有实际执行时间，但审批记录仍为 pending 的异常订单。",
        }]
        return {
            "manifest_version": "chuanshen.semantic-mapping/v1",
            "source_id": source_id,
            "ontology_id": ontology_id,
            "schema_version_id": schema_id,
            "entities": entities,
            "attributes": attributes,
            "relationships": relationships,
            "derived_metrics": derived_metrics,
            "record_sets": record_sets,
            "governed_queries": guolian_governed_query_templates(),
            "notes": [
                DEMO_NOTICE,
                "确定性映射；数据库凭据不进入 Manifest",
                "风险事件影响项目的受控语义路径为 risk-event --risk-product--> product <--project-product-product-- project-product --project-product-project--> project；项目产品关系仅统计 project-product-active=true 的有效关联。若问题指定供应商，必须通过 risk-supplier 关联 supplier，并用 supplier-name 按问题中的供应商名称筛选；不得只查询全体风险，也不得通过供应商订单替代影响路径。",
            ],
        }

    def ensure_database_mapping(
        self,
        *,
        space: Mapping[str, Any],
        source_result: Mapping[str, Any],
        ontology: Mapping[str, Any],
        terms: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        source_id = str(source_result["source_id"])
        mappings = _iter_items(self.api.call("GET", f"/semantic-mappings?source_id={source_id}"))
        mapping = find_by(mappings, "name", "国联集团演示经营数据语义映射")
        if not mapping:
            mapping = self.api.call(
                "POST", "/semantic-mappings",
                json={
                    "source_id": source_id,
                    "ontology_id": ontology["id"],
                    "name": "国联集团演示经营数据语义映射",
                    "description": f"采购、供应商、产品、项目和目标确定性映射。{DEMO_NOTICE}",
                },
            )
            status = "created"
        else:
            status = "existing"
        manifest = self._mapping_manifest(
            source_id=source_id,
            schema_id=str(source_result["schema_version_id"]),
            ontology_id=str(ontology["id"]),
            terms=terms,
        )
        mapping = self.api.call("PUT", f"/semantic-mappings/{mapping['id']}", json={"manifest": manifest})
        version = mapping.get("latest_version") or {}
        validation = self.api.call(
            "POST", f"/semantic-mappings/{mapping['id']}/validate?version_id={version['id']}"
        )
        if not (validation.get("validation") or {}).get("ok"):
            raise DemoError("国联集团演示经营数据语义映射校验未通过")
        mapping = self.api.call(
            "POST", f"/semantic-mappings/{mapping['id']}/activate?version_id={version['id']}"
        )
        self.report.add(
            "database", "ensure-mapping", "国联集团演示经营数据语义映射", status,
            mapping_version_id=mapping.get("active_version_id"),
            entities=len(manifest["entities"]), attributes=len(manifest["attributes"]),
            relationships=len(manifest["relationships"]),
        )
        self.report.resources["mapping_id"] = mapping["id"]
        self.report.resources["mapping_version_id"] = mapping.get("active_version_id")
        return mapping

    def ensure_database_snapshot(self, space: Mapping[str, Any], source_result: Mapping[str, Any]) -> None:
        source_id = str(source_result["source_id"])
        documents = self.api.call("GET", f"/documents?space_id={space['id']}")
        current = next((row for row in documents if row.get("source_id") == source_id), None)
        if current and _current_knowledge_status(self.api, current) == "published":
            self.report.add("database", "sync-or-reuse", "PostgreSQL 演示快照", "existing")
            return
        job = self.api.call("POST", f"/sources/{source_id}/sync")
        completed = self.wait_job(job["id"])
        result = completed.get("result") or {}
        if result.get("parse_job_id"):
            self.wait_job(result["parse_job_id"])
        if result.get("version_id"):
            self.wait_knowledge_job(result["version_id"])
        self.report.add("database", "sync", "PostgreSQL 演示快照", "succeeded")

    def _evidence_chunk(self, space_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
        fact_id = str(spec["fact_id"])
        title = str(spec["source_title"])
        documents = self.api.call("GET", f"/documents?space_id={space_id}")
        document = find_by(documents, "title", title)
        if not document or not document.get("current_version_id"):
            raise DemoError(f"图谱事实 {fact_id} 的证据文档不存在：{title}")
        chunks = _iter_items(
            self.api.call("GET", f"/versions/{document['current_version_id']}/chunks?limit=500")
        )
        expected_path = str(spec["chunk_structural_path"])
        expected_terms = [normalized_evidence_text(value) for value in spec["evidence_terms"]]
        selected = next(
            (
                row for row in chunks
                if str(row.get("structural_path") or "") == expected_path
                and row.get("status") == "published"
                and all(term in normalized_evidence_text(row.get("text")) for term in expected_terms)
            ),
            None,
        )
        if not selected:
            raise DemoError(
                f"图谱事实 {fact_id} 未找到同时匹配文档、结构位置和全部证据词的已发布片段：{title} / {expected_path}"
            )
        return {
            "id": selected["id"],
            "document_id": document["id"],
            "version_id": document["current_version_id"],
            "title": title,
            "structural_path": expected_path,
        }

    def ensure_graph(self, space: Mapping[str, Any]) -> dict[str, Any]:
        # Resolve every asserted fact's exact supporting chunk before making
        # any graph mutation.  This keeps preparation fail-closed: a stale or
        # semantically mismatched fixture cannot leave behind half-seeded
        # entities, facts, or inferred scenarios.
        fact_specs = seeded_graph_ground_truth(self.fixture_root)
        evidence = {
            spec["fact_id"]: self._evidence_chunk(space["id"], spec)
            for spec in fact_specs
        }
        entity_specs = {
            "采购实施细则": "制度",
            "国联集团": "组织",
            "数字科技公司": "组织",
            "数字化管理部": "部门",
            "东方智造": "供应商",
            "NexusOne": "产品",
            "智慧流程中枢项目": "项目",
            "交付延期": "风险事件",
            "智慧流程中枢": "系统",
            "集团数据交换平台": "系统",
            "统一身份组件": "系统",
            "升级维护事件": "风险事件",
        }
        entity_path = f"/knowledge/entities?space_id={space['id']}"
        existing_entities = paginated_items(self.api, entity_path)
        entities: dict[str, dict[str, Any]] = {}
        for name, entity_type in entity_specs.items():
            row = next(
                (
                    item for item in existing_entities
                    if graph_entity_matches(item, name=name, entity_type=entity_type)
                ),
                None,
            )
            if not row:
                try:
                    row = self.api.call(
                        "POST", "/knowledge/entities",
                        json={
                            "space_id": space["id"], "canonical_name": name,
                            "entity_type": entity_type, "confidence": 1,
                            "properties": {"dataset": SPACE_CODE, "synthetic": True, "notice": DEMO_NOTICE},
                        },
                    )
                except DemoError as exc:
                    if "HTTP 409" not in str(exc):
                        raise
                    # Another worker or an earlier interrupted run may have
                    # created the same entity after our first page scan.
                    refreshed = paginated_items(self.api, entity_path)
                    row = next(
                        (
                            item for item in refreshed
                            if graph_entity_matches(item, name=name, entity_type=entity_type)
                        ),
                        None,
                    )
                    if not row:
                        raise
                existing_entities.append(row)
            # Semantic extraction may have created a case-insensitive match
            # such as ``Nexusone`` before the deterministic demo graph runs.
            # Keep that stable entity ID, but use the auditable curation layer
            # to restore the official display spelling instead of creating a
            # duplicate node or writing directly to the database.
            if row.get("canonical_name") != name:
                updated = self.api.call(
                    "PUT", f"/knowledge/entities/{row['id']}",
                    json={
                        "canonical_name": name,
                        "reason_note": "演示空间业务名称规范化",
                    },
                )
                if updated.get("job_id"):
                    self.wait_job(str(updated["job_id"]))
                refreshed = paginated_items(self.api, entity_path)
                row = next(
                    (
                        item for item in refreshed
                        if graph_entity_matches(item, name=name, entity_type=entity_type)
                    ),
                    row,
                )
            entities[name] = row

        # Consolidate exact-name extraction artefacts typed only as “其他”.
        # This uses the normal, auditable entity-pair governance endpoint so
        # all relationships are redirected and the operation remains
        # reversible.  It is intentionally limited to exact normalized names;
        # fuzzy or merely similar business entities still require a person.
        normalized_duplicates_merged = 0
        refreshed_entities = paginated_items(self.api, entity_path)
        for name, entity_type in entity_specs.items():
            winner = entities[name]
            duplicates = [
                item for item in refreshed_entities
                if item.get("id") != winner.get("id")
                and item.get("entity_type") == "其他"
                and normalized_entity_name(item.get("normalized_name") or item.get("canonical_name"))
                == normalized_entity_name(name)
            ]
            for duplicate in duplicates:
                result = self.api.call(
                    "POST", "/curation/entities/pair",
                    json={
                        "space_id": space["id"],
                        "left_entity_id": winner["id"],
                        "right_entity_id": duplicate["id"],
                        "operation": "merge",
                        "winner_entity_id": winner["id"],
                        "reason_note": f"演示空间将同名其他类型节点归一为{name}（{entity_type}）",
                    },
                )
                job = result.get("job") or {}
                if job.get("id"):
                    self.wait_job(str(job["id"]))
                normalized_duplicates_merged += 1

        existing_facts = paginated_items(
            self.api, f"/knowledge/facts?space_id={space['id']}&include_inferred=false"
        )
        # An early demo build used the inverse direction for “负责”.  Keep the
        # historical row and audit trail, but suppress it so every current
        # graph edge consistently models 责任主体 → 负责 → 项目.
        legacy_reverse = [
            row for row in existing_facts
            if row.get("subject_entity_id") == entities["智慧流程中枢项目"]["id"]
            and row.get("predicate") == "负责"
            and row.get("object_entity_id") == entities["数字科技公司"]["id"]
        ]
        legacy_suppressed = 0
        for row in legacy_reverse:
            updated = self.api.call(
                "PUT", f"/knowledge/facts/{row['id']}",
                json={
                    "status": "suppressed",
                    "reason_note": "演示 Ground Truth 关系方向纠正：责任主体应指向所负责项目",
                },
            )
            if updated.get("job_id"):
                self.wait_job(str(updated["job_id"]))
            legacy_suppressed += 1
        if legacy_reverse:
            legacy_ids = {row["id"] for row in legacy_reverse}
            existing_facts = [row for row in existing_facts if row.get("id") not in legacy_ids]

        created = 0
        evidence_repaired = 0
        for spec in fact_specs:
            subject = str(spec["subject"])
            predicate = str(spec["predicate"])
            obj = str(spec["object"])
            for entity_name in (subject, obj):
                if entity_name not in entities:
                    raise DemoError(
                        f"图谱 Ground Truth {spec['fact_id']} 引用了未声明实体：{entity_name}"
                    )
            chunk_id = evidence[str(spec["fact_id"])]["id"]
            duplicate = next(
                (
                    row for row in existing_facts
                    if row.get("subject_entity_id") == entities[subject]["id"]
                    and row.get("predicate") == predicate
                    and row.get("object_entity_id") == entities[obj]["id"]
                ),
                None,
            )
            if duplicate:
                if duplicate.get("source_chunk_id") != chunk_id:
                    updated = self.api.call(
                        "PUT", f"/knowledge/facts/{duplicate['id']}",
                        json={
                            "source_chunk_id": chunk_id,
                            "reason_note": f"演示 Ground Truth 证据对齐：{spec['fact_id']}",
                        },
                    )
                    if updated.get("job_id"):
                        self.wait_job(str(updated["job_id"]))
                    evidence_repaired += 1
                continue
            self.api.call(
                "POST", "/knowledge/facts",
                json={
                    "space_id": space["id"],
                    "subject_entity_id": entities[subject]["id"],
                    "predicate": predicate,
                    "object_entity_id": entities[obj]["id"],
                    "source_chunk_id": chunk_id,
                    "confidence": 1,
                    "status": "published",
                },
            )
            created += 1
        self.report.add(
            "graph", "ensure", "演示实体与证据关系", "succeeded",
            entities=len(entities), facts=len(fact_specs), created=created,
            evidence_repaired=evidence_repaired,
            legacy_reverse_suppressed=legacy_suppressed,
            normalized_duplicates_merged=normalized_duplicates_merged,
        )
        self.report.resources["graph"] = {
            "entities": len(entities),
            "facts": len(fact_specs),
            "created": created,
            "evidence_repaired": evidence_repaired,
            "legacy_reverse_suppressed": legacy_suppressed,
        }
        return self.report.resources["graph"]

    def ensure_analysis(self, space: Mapping[str, Any]) -> list[dict[str, Any]]:
        definitions = (
            {
                "name": "演示·制度适用范围",
                "question": "采购实施细则是否适用于数字科技公司？",
                "category": "制度适用范围",
                "template_id": "policy-applicability",
                "rule_name": "制度向下属单位传导",
                "definition": {
                    "conditions": [
                        {"predicate": "适用于", "subject": "R1", "object": "R2"},
                        {"predicate": "管理", "subject": "R2", "object": "R3"},
                    ],
                    "conclusion": {"predicate": "适用于", "subject": "R1", "object": "R3"},
                },
                "role_labels": {"R1": "制度", "R2": "上级组织", "R3": "下属单位"},
            },
            {
                "name": "演示·供应商风险传导",
                "question": "东方智造交付延期会影响哪些项目？",
                "category": "供应商风险",
                "template_id": "supplier-risk-propagation",
                "rule_name": "供应商风险传导到项目",
                "definition": {
                    "conditions": [
                        {"predicate": "供应", "subject": "R1", "object": "R2"},
                        {"predicate": "用于", "subject": "R2", "object": "R3"},
                        {"predicate": "存在风险", "subject": "R1", "object": "R4"},
                    ],
                    "conclusion": {"predicate": "受到影响", "subject": "R3", "object": "R4"},
                },
                "role_labels": {"R1": "供应商", "R2": "产品", "R3": "项目", "R4": "风险"},
            },
            {
                "name": "演示·系统依赖影响",
                "question": "统一身份组件维护可能影响哪些系统？",
                "category": "系统依赖",
                "template_id": "system-dependency-impact",
                "rule_name": "组件维护沿系统依赖传导",
                "definition": {
                    "conditions": [
                        {"predicate": "依赖", "subject": "R1", "object": "R2"},
                        {"predicate": "使用", "subject": "R2", "object": "R3"},
                        {"predicate": "存在风险", "subject": "R3", "object": "R4"},
                    ],
                    "conclusion": {"predicate": "受到影响", "subject": "R1", "object": "R4"},
                },
                "role_labels": {"R1": "业务系统", "R2": "被依赖系统", "R3": "基础组件", "R4": "维护事件"},
            },
        )
        existing = {
            row["name"]: row
            for row in self.api.call("GET", "/analysis/tasks")
            if space["id"] in (row.get("space_ids") or [])
        }
        results: list[dict[str, Any]] = []
        for definition in definitions:
            if definition["name"] in existing:
                task = existing[definition["name"]]
                # Inference evidence is an immutable snapshot of the asserted
                # facts used by a run.  Reusing a historical successful run
                # after document versions or fact evidence have changed would
                # make the demo appear healthy while still showing stale
                # provenance.  The explicit ``analysis`` preparation phase is
                # therefore a verification run: keep the historical run for
                # audit, execute Semantica again against the current graph,
                # and make that completed run the task's latest projection.
                previous = task.get("last_run") or {}
                if previous.get("status") in {"queued", "running"} and previous.get("job_id"):
                    self.wait_job(str(previous["job_id"]))
                run = self.api.call(
                    "POST",
                    f"/analysis/tasks/{task['id']}/run",
                    json={"mode": "preview", "max_results": 100},
                )
                if run.get("job_id"):
                    self.wait_job(str(run["job_id"]))
                results.append(
                    {
                        "name": definition["name"],
                        "status": "verified-rerun",
                        "task_id": task["id"],
                        "run_id": run.get("id"),
                    }
                )
                continue
            created = self.api.call(
                "POST", "/analysis/guided-setups",
                json={
                    **definition,
                    "space_id": space["id"],
                    "confidence": 1,
                    "priority": 100,
                    "auto_run": False,
                    "auto_publish": False,
                    "mode": "preview",
                    "max_results": 100,
                },
            )
            run = created.get("run") or {}
            if run.get("job_id"):
                self.wait_job(run["job_id"])
            results.append(
                {
                    "name": definition["name"],
                    "status": "created",
                    "task_id": created["task"]["id"],
                    "run_id": run.get("id"),
                }
            )
        self.report.add("analysis", "ensure", "Semantica 规则推演任务", "succeeded", tasks=len(results))
        self.report.resources["analysis_tasks"] = len(results)
        return results

    def prepare(self, phases: Sequence[str]) -> PrepareReport:
        requested = set(phases)
        space = self.ensure_space()
        if "identity" in requested:
            self.ensure_identity(space)
        media_policy: dict[str, Any] = {"id": space.get("media_policy_id")}
        if "models" in requested:
            self.ensure_model_readiness()
        if "models" in requested or "documents" in requested:
            media_policy = self.ensure_media_policy(space)
        ontology: dict[str, Any] | None = None
        terms: dict[str, Any] | None = None
        if "ontology" in requested or "database" in requested:
            ontology, terms = self.ensure_ontology(space)
        if "documents" in requested:
            self.ensure_documents(space, media_policy)
        if "sources" in requested:
            self.ensure_account_free_sources(space)
        if "database" in requested:
            database = self.ensure_database_sources(space)
            if database:
                assert ontology is not None and terms is not None
                self.ensure_database_mapping(
                    space=space,
                    source_result=database["postgresql"],
                    ontology=ontology,
                    terms=terms,
                )
                self.ensure_database_snapshot(space, database["postgresql"])
        if "graph" in requested:
            self.ensure_graph(space)
        if "analysis" in requested:
            self.ensure_analysis(space)
        return self.report


def validate_reset_confirmation(confirm: str | None, execute: bool) -> None:
    if execute and confirm != SPACE_CODE:
        raise DemoError(f"执行重置必须同时提供 --confirm {SPACE_CODE}")


def _space_ids_from_job(job: Mapping[str, Any]) -> set[str]:
    payload = job.get("input") or {}
    result: set[str] = set()
    for key in ("space_id",):
        if payload.get(key):
            result.add(str(payload[key]))
    for value in payload.get("space_ids") or []:
        result.add(str(value))
    return result


class DemoResetter:
    """Dependency-aware reset restricted to the dedicated demo space."""

    def __init__(self, api: ApiLike) -> None:
        self.api = api

    def locate_space(self) -> dict[str, Any] | None:
        return find_by(self.api.call("GET", "/spaces"), "code", SPACE_CODE)

    def inventory(self, space: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        space_id = space["id"]
        documents = _iter_items(self.api.call("GET", f"/documents?space_id={space_id}"))
        sources = _iter_items(self.api.call("GET", f"/sources?space_id={space_id}"))
        mappings = _iter_items(self.api.call("GET", f"/semantic-mappings?space_id={space_id}"))
        ontologies = [
            row for row in _iter_items(self.api.call("GET", f"/ontologies?space_id={space_id}"))
            if row.get("space_id") == space_id
        ]
        entities = _iter_items(self.api.call("GET", f"/knowledge/entities?space_id={space_id}&limit=500"))
        facts = _iter_items(self.api.call("GET", f"/knowledge/facts?space_id={space_id}&limit=500"))
        scenarios = [
            row for row in _iter_items(self.api.call("GET", "/analysis/scenarios"))
            if space_id in (row.get("space_ids") or [])
        ]
        rule_sets = [
            row for row in _iter_items(self.api.call("GET", "/analysis/rule-sets"))
            if space_id in (row.get("space_ids") or [])
        ]
        saved_queries = [
            row for row in _iter_items(self.api.call("GET", "/analysis/saved-queries"))
            if space_id in (row.get("space_ids") or [])
        ]
        conversations = [
            row for row in _iter_items(self.api.call("GET", "/conversations"))
            if space_id in (row.get("space_ids") or [])
        ]
        inference_runs = [
            row for row in _iter_items(self.api.call("GET", "/analysis/inference-runs"))
            if space_id in (row.get("space_ids") or [])
        ]
        source_ids = {row["id"] for row in sources}
        version_ids: set[str] = set()
        for document in documents:
            detail = self.api.call("GET", f"/documents/{document['id']}")
            version_ids.update(row["id"] for row in detail.get("versions") or [])
        active_jobs = []
        for job in _iter_items(self.api.call("GET", "/jobs")):
            if job.get("status") not in {"queued", "running"}:
                continue
            payload = job.get("input") or {}
            if (
                space_id in _space_ids_from_job(job)
                or payload.get("source_id") in source_ids
                or payload.get("version_id") in version_ids
            ):
                active_jobs.append(job)
        active_job_ids = {str(row.get("id")) for row in active_jobs}
        for run in inference_runs:
            if run.get("status") not in {"queued", "running"} and run.get("job_status") not in {"queued", "running"}:
                continue
            job_id = str(run.get("job_id") or f"inference:{run.get('id')}")
            if job_id not in active_job_ids:
                active_jobs.append(
                    {"id": job_id, "status": run.get("job_status") or run.get("status"), "job_type": "knowledge_inference"}
                )
                active_job_ids.add(job_id)
        return {
            "conversations": conversations,
            "saved_queries": saved_queries,
            "scenarios": scenarios,
            "rule_sets": rule_sets,
            "mappings": mappings,
            "facts": facts,
            "entities": entities,
            "ontologies": ontologies,
            "sources": sources,
            "documents": documents,
            "inference_runs": inference_runs,
            "active_jobs": active_jobs,
        }

    def reset(self, *, execute: bool, confirm: str | None) -> dict[str, Any]:
        validate_reset_confirmation(confirm, execute)
        space = self.locate_space()
        if not space:
            return {"space_code": SPACE_CODE, "status": "absent", "execute": execute, "counts": {}}
        if space.get("code") != SPACE_CODE:
            raise DemoError("安全检查失败：目标空间编码不匹配")
        inventory = self.inventory(space)
        counts = {key: len(value) for key, value in inventory.items()}
        result: dict[str, Any] = {
            "space_code": SPACE_CODE,
            "space_id": space["id"],
            "status": "planned" if not execute else "running",
            "execute": execute,
            "counts": counts,
        }
        if not execute:
            return result
        if inventory["active_jobs"]:
            raise DemoError("演示空间仍有运行中任务；请等待任务结束后再重置")

        endpoints = (
            ("conversations", "/conversations/{id}"),
            ("saved_queries", "/analysis/saved-queries/{id}"),
            ("scenarios", "/analysis/scenarios/{id}"),
            ("rule_sets", "/analysis/rule-sets/{id}"),
            ("mappings", "/semantic-mappings/{id}"),
            ("facts", "/knowledge/facts/{id}"),
            ("entities", "/knowledge/entities/{id}"),
            ("ontologies", "/ontologies/{id}"),
            ("sources", "/sources/{id}"),
            ("documents", "/documents/{id}"),
        )
        deleted: dict[str, int] = {}
        for key, template in endpoints:
            deleted[key] = 0
            for row in inventory[key]:
                self.api.call("DELETE", template.format(id=row["id"]))
                deleted[key] += 1
        self.api.call("DELETE", f"/spaces/{space['id']}")
        result.update({"status": "deleted", "deleted": deleted})
        return result
