from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from apps.api.structured_schemas import SemanticQueryIR, SemanticQueryPlan
from packages.platform.semantic_mapping import find_semantic_relationship_path
from packages.platform.models import DataSourceSchemaVersion, SemanticMappingVersion, SourceConnector
from packages.platform.structured_query import (
    apply_activated_metric_contracts,
    compile_structured_query,
    generate_semantic_plan_ir,
    validate_ir,
)
from scripts.demo.guolian_demo import (
    DEMO_NOTICE,
    DEMO_ROUTING_POLICY_NAME,
    SPACE_CODE,
    DemoError,
    DemoPreparer,
    DemoResetter,
    SafeApiClient,
    build_prepare_plan,
    discover_demo_documents,
    paginated_items,
    normalized_entity_name,
    redact,
    safe_public_url,
    validate_reset_confirmation,
)


class SourcePreparationApi:
    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.sync_counts: dict[str, int] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.startswith("/sources?"):
            return [dict(row) for row in self.sources]
        if method == "POST" and path == "/sources":
            payload = dict(kwargs["json"])
            payload.pop("secret", None)
            source = {"id": f"source-{len(self.sources) + 1}", **payload}
            self.sources.append(source)
            return dict(source)
        if method == "PUT" and path.startswith("/sources/"):
            source = next(row for row in self.sources if row["id"] == path.rsplit("/", 1)[-1])
            payload = dict(kwargs["json"])
            payload.pop("secret", None)
            source.update(payload)
            return dict(source)
        if method == "POST" and path == "/sources/test":
            assert "secret" not in kwargs["json"]
            return {"status": "success", "bytes": 128, "elapsed_ms": 3}
        if method == "POST" and path.endswith("/sync"):
            source_id = path.split("/")[2]
            attempt = self.sync_counts.get(source_id, 0) + 1
            self.sync_counts[source_id] = attempt
            sync_id = f"sync-{source_id}-{attempt}"
            document_id = f"document-{source_id}"
            version_id = f"version-{source_id}"
            if attempt == 1:
                self.documents.append(
                    {
                        "id": document_id,
                        "source_id": source_id,
                        "title": document_id,
                        "current_version_id": version_id,
                        "versions": [
                            {
                                "id": version_id,
                                "parse_summary": {
                                    "knowledge_status": "published",
                                    "knowledge_processing_mode": "both",
                                },
                            }
                        ],
                    }
                )
                parse_id = f"parse-{source_id}"
                knowledge_id = f"knowledge-{source_id}"
                self.jobs[parse_id] = {"id": parse_id, "status": "succeeded"}
                self.jobs[knowledge_id] = {
                    "id": knowledge_id,
                    "job_type": "process_knowledge",
                    "status": "succeeded",
                    "input": {"version_id": version_id},
                }
                result = {
                    "document_id": document_id,
                    "version_id": version_id,
                    "parse_job_id": parse_id,
                    "unchanged": False,
                }
            else:
                result = {
                    "document_id": document_id,
                    "version_id": version_id,
                    "parse_job_id": None,
                    "unchanged": True,
                }
            self.jobs[sync_id] = {"id": sync_id, "status": "succeeded", "result": result}
            return {"id": sync_id}
        if method == "GET" and path == "/jobs":
            return [dict(row) for row in self.jobs.values()]
        if method == "GET" and path.startswith("/jobs/"):
            return dict(self.jobs[path.rsplit("/", 1)[-1]])
        if method == "GET" and path.startswith("/documents?"):
            return [dict(row) for row in self.documents]
        if method == "GET" and path.startswith("/documents/"):
            document = next(row for row in self.documents if row["id"] == path.rsplit("/", 1)[-1])
            return dict(document)
        if method == "PUT" and path.startswith("/documents/"):
            document = next(row for row in self.documents if row["id"] == path.rsplit("/", 1)[-1])
            document.update(kwargs["json"])
            return dict(document)
        raise AssertionError((method, path, kwargs))


class FailedMediaModelsApi:
    def __init__(self) -> None:
        self.policy_payload: dict[str, Any] | None = None

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET" and path == "/model-configs":
            return [
                {
                    "id": "asr-failed", "model_kind": "asr", "enabled": True,
                    "is_default": True, "last_test_status": "failed",
                },
                {
                    "id": "vision-failed", "model_kind": "vision", "enabled": True,
                    "is_default": True, "last_test_status": "failed",
                },
            ]
        if method == "GET" and path == "/media-policies":
            return []
        if method == "POST" and path == "/media-policies":
            self.policy_payload = dict(kwargs["json"])
            return {"id": "media-policy", **self.policy_payload}
        if method == "POST" and path == "/media-policies/media-policy/validate":
            return {"ok": True}
        if method == "PUT" and path == "/spaces/space-demo":
            return {"id": "space-demo", **kwargs["json"]}
        raise AssertionError((method, path, kwargs))


class ChangedDemoDocumentApi:
    def __init__(self, *, title: str, old_sha256: str) -> None:
        self.document = {
            "id": "document-approval",
            "title": title,
            "current_version_id": "version-1",
            "tags": [],
        }
        self.versions = [
            {
                "id": "version-1",
                "version_number": 1,
                "sha256": old_sha256,
                "parse_summary": {"knowledge_status": "published"},
            }
        ]
        self.uploads: list[dict[str, Any]] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET" and path.startswith("/documents?"):
            return [dict(self.document)]
        if method == "GET" and path == "/documents/document-approval":
            return {
                **self.document,
                "versions": [dict(row) for row in self.versions],
            }
        if method == "POST" and path == "/documents/upload":
            payload = dict(kwargs["data"])
            name, content, _ = kwargs["files"]["file"]
            assert payload["document_id"] == self.document["id"]
            version = {
                "id": "version-2",
                "version_number": 2,
                "sha256": hashlib.sha256(content).hexdigest(),
                "parse_summary": {"knowledge_status": "published"},
            }
            self.document["current_version_id"] = version["id"]
            self.versions.insert(0, version)
            self.uploads.append({"name": name, "content": content, "data": payload})
            return {
                "document": dict(self.document),
                "version": dict(version),
                "job": {"id": "parse-version-2"},
            }
        if method == "PUT" and path == "/documents/document-approval":
            self.document.update(kwargs["json"])
            return dict(self.document)
        raise AssertionError((method, path, kwargs))


class PartialSourcePipelineApi:
    def __init__(self, *, unchanged: bool, retry_outcomes: list[str]) -> None:
        self.version_id = "version-partial"
        self.document_id = "document-partial"
        self.retry_outcomes = list(retry_outcomes)
        self.process_calls = 0
        self.document = {
            "id": self.document_id,
            "current_version_id": self.version_id,
            "versions": [
                {
                    "id": self.version_id,
                    "parse_summary": {
                        "knowledge_status": "partial_failed",
                        "knowledge_processing_mode": "vector",
                        "knowledge_processing_requested_mode": "both",
                    },
                }
            ],
        }
        result = {
            "document_id": self.document_id,
            "version_id": self.version_id,
            "parse_job_id": None if unchanged else "parse-partial",
            "unchanged": unchanged,
        }
        self.jobs: dict[str, dict[str, Any]] = {
            "sync-partial": {"id": "sync-partial", "status": "succeeded", "result": result},
        }
        if not unchanged:
            self.jobs["parse-partial"] = {"id": "parse-partial", "status": "succeeded"}
            self.jobs["knowledge-partial"] = {
                "id": "knowledge-partial",
                "job_type": "process_knowledge",
                "status": "failed",
                "input": {"version_id": self.version_id},
                "error_code": "SEMANTIC_EXTRACTION_PARTIAL",
                "error_message": "图谱抽取仅部分完成",
            }

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET" and path == "/jobs":
            return [dict(row) for row in self.jobs.values()]
        if method == "GET" and path.startswith("/jobs/"):
            return dict(self.jobs[path.rsplit("/", 1)[-1]])
        if method == "GET" and path == f"/documents/{self.document_id}":
            return {
                **self.document,
                "versions": [
                    {**version, "parse_summary": dict(version["parse_summary"])}
                    for version in self.document["versions"]
                ],
            }
        if method == "POST" and path == f"/documents/{self.document_id}/process?force=true":
            # A partial `both` run records only the completed vector target as
            # its effective mode.  Preparation must preserve the originally
            # requested target set while repairing the missing graph target.
            assert kwargs["json"] == {"mode": "both"}
            self.process_calls += 1
            outcome = self.retry_outcomes.pop(0) if self.retry_outcomes else "failed"
            job_id = f"knowledge-retry-{self.process_calls}"
            job = {
                "id": job_id,
                "job_type": "process_knowledge",
                "status": outcome,
                "input": {"version_id": self.version_id},
            }
            if outcome == "succeeded":
                self.document["versions"][0]["parse_summary"].update(
                    {
                        "knowledge_status": "published",
                        "knowledge_processing_mode": "both",
                    }
                )
            else:
                job.update(
                    {
                        "error_code": "SEMANTIC_EXTRACTION_PARTIAL",
                        "error_message": "图谱抽取仍未完整完成",
                    }
                )
            self.jobs[job_id] = job
            return {"id": job_id}
        raise AssertionError((method, path, kwargs))


class FakeMinio:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(self, bucket: str, name: str, stream, length: int, **_kwargs: Any) -> None:
        body = stream.read()
        assert len(body) == length
        self.objects[(bucket, name)] = body


class GraphPaginationApi:
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
    fact_specs = (
        ("采购实施细则", "适用于", "国联集团", "chunk-policy"),
        ("国联集团", "管理", "数字科技公司", "chunk-premise"),
        ("东方智造", "供应", "NexusOne", "chunk-policy"),
        ("NexusOne", "用于", "智慧流程中枢项目", "chunk-policy"),
        ("数字科技公司", "负责", "智慧流程中枢项目", "chunk-rst"),
        ("东方智造", "存在风险", "交付延期", "chunk-email"),
        ("智慧流程中枢", "依赖", "集团数据交换平台", "chunk-premise"),
        ("集团数据交换平台", "使用", "统一身份组件", "chunk-premise"),
        ("统一身份组件", "存在风险", "升级维护事件", "chunk-premise"),
    )

    def __init__(
        self,
        *,
        conflict_name: str | None = None,
        broken_evidence: bool = False,
        stale_evidence: bool = False,
        legacy_reverse: bool = False,
    ) -> None:
        self.conflict_name = conflict_name
        self.conflict_raised = False
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.entities = [
            {
                "id": f"filler-{index}", "canonical_name": f"填充实体-{index}",
                "entity_type": "填充类型",
            }
            for index in range(505)
        ]
        for name, entity_type in self.entity_specs.items():
            if name != conflict_name:
                canonical_name = "Nexusone" if name == "NexusOne" else name
                self.entities.append(
                    {
                        "id": f"entity-{name}", "canonical_name": canonical_name,
                        "normalized_name": normalized_entity_name(canonical_name),
                        "entity_type": entity_type,
                    }
                )
        self.entities.append(
            {
                "id": "entity-NexusOne-other",
                "canonical_name": "Nexusone",
                "normalized_name": "nexusone",
                "entity_type": "其他",
            }
        )
        self.facts = [
            {
                "id": f"filler-fact-{index}", "subject_entity_id": "filler-0",
                "predicate": f"填充关系-{index}", "object_entity_id": "filler-1",
            }
            for index in range(505)
        ]
        self.facts.extend(
            {
                "id": f"fact-{index}",
                "subject_entity_id": f"entity-{subject}",
                "predicate": predicate,
                "object_entity_id": f"entity-{obj}",
                "source_chunk_id": source_chunk_id,
            }
            for index, (subject, predicate, obj, source_chunk_id) in enumerate(self.fact_specs)
        )
        if stale_evidence:
            self.facts[505]["source_chunk_id"] = "legacy-unrelated-chunk"
        if legacy_reverse:
            self.facts.append(
                {
                    "id": "legacy-reverse-responsibility",
                    "subject_entity_id": "entity-智慧流程中枢项目",
                    "predicate": "负责",
                    "object_entity_id": "entity-数字科技公司",
                    "source_chunk_id": "legacy-unrelated-chunk",
                }
            )
        self.documents = [
            {"id": "policy-doc", "title": "集团本部采购实施细则（2025演示现行版）.md", "current_version_id": "policy-version"},
            {"id": "rst-doc", "title": "智慧流程中枢建设说明（演示版）.rst", "current_version_id": "rst-version"},
            {"id": "risk-doc", "title": "东方智造交付延期通知（演示版）.eml", "current_version_id": "email-version"},
            {"id": "premise-doc", "title": "规则推演前提事实（演示版）.txt", "current_version_id": "premise-version"},
        ]
        self.chunks = {
            "policy-version": {
                "id": "chunk-policy", "status": "published", "structural_path": "document",
                "text": (
                    "采购实施细则适用于国联集团。"
                    "东方智造供应 NexusOne，NexusOne 用于智慧流程中枢项目。"
                ),
            },
            "rst-version": {
                "id": "chunk-rst", "status": "published", "structural_path": "document",
                "text": "智慧流程中枢项目由数字科技公司负责。",
            },
            "email-version": {
                "id": "chunk-email", "status": "published", "structural_path": "email/body",
                "text": "东方智造存在风险交付延期。",
            },
            "premise-version": {
                "id": "chunk-premise", "status": "published", "structural_path": "document",
                "text": (
                    "国联集团管理数字科技公司。"
                    "智慧流程中枢依赖集团数据交换平台。"
                    "集团数据交换平台使用统一身份组件。"
                    "统一身份组件存在风险升级维护事件。"
                ),
            },
        }
        if broken_evidence:
            self.chunks["premise-version"]["text"] = "只有不相关的演示说明。"
        self.jobs: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _page(path: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        query = parse_qs(urlsplit(path).query)
        offset = int(query.get("offset", [0])[0])
        limit = int(query.get("limit", [100])[0])
        return {"total": len(rows), "items": [dict(row) for row in rows[offset:offset + limit]]}

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.startswith("/knowledge/entities?"):
            return self._page(path, self.entities)
        if method == "GET" and path.startswith("/knowledge/facts?"):
            return self._page(path, self.facts)
        if method == "POST" and path == "/knowledge/entities":
            payload = kwargs["json"]
            if payload["canonical_name"] == self.conflict_name and not self.conflict_raised:
                self.conflict_raised = True
                self.entities.append(
                    {
                        "id": f"entity-{self.conflict_name}",
                        "canonical_name": "Nexusone" if self.conflict_name == "NexusOne" else self.conflict_name,
                        "normalized_name": normalized_entity_name(self.conflict_name),
                        "entity_type": payload["entity_type"],
                    }
                )
                raise DemoError("POST /knowledge/entities 返回 HTTP 409：节点已存在")
            raise AssertionError(f"unexpected entity creation: {payload}")
        if method == "PUT" and path.startswith("/knowledge/entities/"):
            entity_id = path.rsplit("/", 1)[-1]
            entity = next(row for row in self.entities if row["id"] == entity_id)
            entity["canonical_name"] = kwargs["json"]["canonical_name"]
            entity["normalized_name"] = normalized_entity_name(entity["canonical_name"])
            job_id = f"curation-{entity_id}"
            self.jobs[job_id] = {"id": job_id, "status": "succeeded"}
            return {**entity, "job_id": job_id}
        if method == "PUT" and path.startswith("/knowledge/facts/"):
            fact_id = path.rsplit("/", 1)[-1]
            fact = next(row for row in self.facts if row["id"] == fact_id)
            fact.update({key: value for key, value in kwargs["json"].items() if key != "reason_note"})
            job_id = f"fact-curation-{fact_id}"
            self.jobs[job_id] = {"id": job_id, "status": "succeeded"}
            return {**fact, "job_id": job_id}
        if method == "POST" and path == "/curation/entities/pair":
            payload = kwargs["json"]
            assert payload["operation"] == "merge"
            winner_id = payload["winner_entity_id"]
            loser_id = (
                payload["right_entity_id"]
                if payload["left_entity_id"] == winner_id
                else payload["left_entity_id"]
            )
            self.entities = [row for row in self.entities if row["id"] != loser_id]
            for fact in self.facts:
                if fact.get("subject_entity_id") == loser_id:
                    fact["subject_entity_id"] = winner_id
                if fact.get("object_entity_id") == loser_id:
                    fact["object_entity_id"] = winner_id
            job_id = f"merge-{loser_id}"
            self.jobs[job_id] = {"id": job_id, "status": "succeeded"}
            return {"job": {"id": job_id}}
        if method == "POST" and path == "/knowledge/facts":
            raise AssertionError(f"unexpected fact creation: {kwargs['json']}")
        if method == "GET" and path.startswith("/documents?"):
            return [dict(row) for row in self.documents]
        if method == "GET" and path.startswith("/versions/") and "/chunks?" in path:
            version = path.split("/")[2]
            return {"items": [dict(self.chunks[version])]}
        if method == "GET" and path.startswith("/jobs/"):
            return dict(self.jobs[path.rsplit("/", 1)[-1]])
        raise AssertionError((method, path, kwargs))


class SpaceApi:
    def __init__(self) -> None:
        self.spaces: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path == "/spaces":
            return list(self.spaces)
        if method == "POST" and path == "/spaces":
            row = {"id": "space-demo", **kwargs["json"]}
            self.spaces.append(row)
            return row
        if method == "PUT" and path == "/spaces/space-demo":
            self.spaces[0].update(kwargs["json"])
            return dict(self.spaces[0])
        raise AssertionError((method, path))


class ResetApi:
    def __init__(self, *, active_job: bool = False, active_inference: bool = False) -> None:
        self.deleted: list[str] = []
        self.space = {"id": "demo-space-id", "code": SPACE_CODE, "name": "演示空间"}
        self.active_job = active_job
        self.active_inference = active_inference

    def call(self, method: str, path: str, **_kwargs: Any) -> Any:
        if method == "DELETE":
            assert "other-space" not in path
            self.deleted.append(path)
            return {"ok": True}
        if path == "/spaces":
            return [self.space, {"id": "other-space", "code": "customer-space"}]
        if path.startswith("/documents?"):
            return []
        if path.startswith("/sources?"):
            return []
        if path.startswith("/semantic-mappings?"):
            return {"items": []}
        if path.startswith("/ontologies?"):
            return []
        if path.startswith("/knowledge/entities?") or path.startswith("/knowledge/facts?"):
            return {"items": []}
        if path in {"/analysis/scenarios", "/analysis/rule-sets", "/analysis/saved-queries"}:
            return []
        if path == "/analysis/inference-runs":
            return ([{
                "id": "run-active", "status": "running", "space_ids": ["demo-space-id"],
                "job_id": "inference-job", "job_status": "running",
            }] if self.active_inference else [])
        if path == "/conversations":
            return {"items": []}
        if path == "/jobs":
            return ([{
                "id": "active", "status": "running", "input": {"space_id": "demo-space-id"},
            }] if self.active_job else [])
        raise AssertionError((method, path))


class ModelReadinessApi:
    def __init__(self) -> None:
        self.models = [
            {"id": "qwen", "name": "在线千问", "model_kind": "llm", "model_name": "qwen3.5-plus", "provider": "openai_compatible", "enabled": True, "is_default": True, "last_test_status": None},
            {"id": "embedding", "name": "本地向量", "model_kind": "embedding", "model_name": "bge", "provider": "bge", "enabled": True, "is_default": True, "last_test_status": None},
            {"id": "vision", "name": "在线视觉", "model_kind": "vision", "model_name": "qwen3.5-plus", "provider": "openai_compatible", "enabled": True, "is_default": True, "last_test_status": None},
            {"id": "asr", "name": "本地语音", "model_kind": "asr", "model_name": "sensevoice", "provider": "openai_compatible", "enabled": True, "is_default": True, "last_test_status": None},
        ]
        self.route_updates: list[dict[str, Any]] = []
        self.extraction_updates: list[dict[str, Any]] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET" and path == "/model-configs":
            return [dict(row) for row in self.models]
        if method == "POST" and path.startswith("/model-configs/") and path.endswith("/test"):
            model_id = path.split("/")[2]
            next(row for row in self.models if row["id"] == model_id)["last_test_status"] = "success"
            return {"status": "success", "elapsed_ms": 12}
        if method == "GET" and path == "/model-routing-policies/resolved":
            return {
                "routes": [
                    {"scene": "agent_chat", "label": "智能问答", "model_config_id": "qwen"},
                    {"scene": "semantic_extract", "label": "语义抽取", "model_config_id": "qwen"},
                    {"scene": "document_governance", "label": "文档治理", "model_config_id": "qwen"},
                    {"scene": "structured_query", "label": "结构化查询", "model_config_id": "qwen"},
                    {"scene": "embedding", "label": "向量化", "model_config_id": "embedding"},
                    {"scene": "vision_understanding", "label": "视觉理解", "model_config_id": "vision"},
                    {"scene": "speech_recognition", "label": "语音识别", "model_config_id": "asr"},
                    {"scene": "reranking", "label": "检索重排", "model_config_id": None},
                ]
            }
        if method == "GET" and path == "/model-routing-policies":
            return [{"id": "route", "name": "默认路由", "enabled": True, "is_default": True, "routes": {}}]
        if method == "PUT" and path == "/model-routing-policies/route":
            self.route_updates.append(kwargs["json"])
            return {"id": "route", "name": "默认路由", **kwargs["json"]}
        if method == "GET" and path == "/extraction-policies":
            return [{"id": "extract", "name": "默认抽取", "enabled": True, "is_default": True, "model_config_id": "missing"}]
        if method == "PUT" and path == "/extraction-policies/extract":
            self.extraction_updates.append(kwargs["json"])
            return {"ok": True}
        raise AssertionError((method, path))


class CleanInstallModelReadinessApi(ModelReadinessApi):
    def __init__(self) -> None:
        super().__init__()
        self.models = [
            {"id": "kimi", "name": "Kimi K3", "model_kind": "llm", "model_name": "kimi-k3", "provider": "kimi", "enabled": True, "is_default": True, "last_test_status": None},
            {"id": "kimi-vision", "name": "Kimi 视觉", "model_kind": "vision", "model_name": "kimi-k3", "provider": "kimi", "enabled": True, "is_default": True, "last_test_status": None},
            {"id": "embedding", "name": "本地向量", "model_kind": "embedding", "model_name": "bge", "provider": "bge", "enabled": True, "is_default": True, "last_test_status": None},
            {"id": "asr", "name": "本地语音", "model_kind": "asr", "model_name": "sensevoice", "provider": "openai_compatible", "enabled": True, "is_default": True, "last_test_status": None},
        ]
        self.secret_seen = False

    def _resolved(self) -> dict[str, Any]:
        defaults = {
            row["model_kind"]: row
            for row in self.models
            if row.get("enabled") and row.get("is_default")
        }
        kinds = {
            "agent_chat": "llm", "semantic_extract": "llm",
            "document_governance": "llm", "structured_query": "llm",
            "embedding": "embedding", "vision_understanding": "vision",
            "speech_recognition": "asr", "reranking": "reranker",
        }
        return {
            "routes": [
                {
                    "scene": scene,
                    "label": scene,
                    "model_config_id": (defaults.get(kind) or {}).get("id"),
                }
                for scene, kind in kinds.items()
            ]
        }

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET" and path == "/model-configs":
            return [dict(row) for row in self.models]
        if method == "POST" and path == "/model-configs":
            payload = dict(kwargs["json"])
            if payload.get("api_key"):
                self.secret_seen = True
            payload.pop("api_key", None)
            payload["id"] = "qwen" if payload["model_kind"] == "llm" else "qwen-vision"
            payload["last_test_status"] = None
            self.models.append(payload)
            return dict(payload)
        if method == "PUT" and path.startswith("/model-configs/"):
            model_id = path.rsplit("/", 1)[-1]
            row = next(item for item in self.models if item["id"] == model_id)
            payload = dict(kwargs["json"])
            if payload.get("api_key"):
                self.secret_seen = True
            payload.pop("api_key", None)
            if payload.get("is_default"):
                for item in self.models:
                    if item["model_kind"] == row["model_kind"]:
                        item["is_default"] = False
            row.update(payload)
            return dict(row)
        if method == "POST" and path.startswith("/model-configs/") and path.endswith("/test"):
            model_id = path.split("/")[2]
            row = next(item for item in self.models if item["id"] == model_id)
            row["last_test_status"] = "failed" if model_id.startswith("kimi") else "success"
            return {"status": row["last_test_status"], "elapsed_ms": 12}
        if method == "GET" and path == "/model-routing-policies/resolved":
            return self._resolved()
        if method == "GET" and path == "/model-routing-policies":
            return [{"id": "route", "name": "默认路由", "enabled": True, "is_default": True, "routes": {}}]
        if method == "PUT" and path == "/model-routing-policies/route":
            self.route_updates.append(kwargs["json"])
            return {"id": "route", "name": "默认路由", **kwargs["json"]}
        if method == "GET" and path == "/extraction-policies":
            return [{"id": "extract", "name": "默认抽取", "enabled": True, "is_default": True, "model_config_id": "kimi"}]
        if method == "PUT" and path == "/extraction-policies/extract":
            self.extraction_updates.append(kwargs["json"])
            return {"ok": True}
        raise AssertionError((method, path, kwargs))


class ExistingAnalysisTasksApi:
    def __init__(self) -> None:
        names = (
            "演示·制度适用范围",
            "演示·供应商风险传导",
            "演示·系统依赖影响",
        )
        self.tasks = [
            {
                "id": f"task-{index}",
                "name": name,
                "space_ids": ["space-demo"],
                "last_run": {
                    "id": f"old-run-{index}",
                    "status": "succeeded",
                    "job_status": "succeeded",
                },
            }
            for index, name in enumerate(names, start=1)
        ]
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path == "/analysis/tasks":
            return [dict(row) for row in self.tasks]
        if method == "POST" and path.startswith("/analysis/tasks/") and path.endswith("/run"):
            task_id = path.split("/")[3]
            assert kwargs["json"] == {"mode": "preview", "max_results": 100}
            return {"id": f"new-run-{task_id}", "job_id": f"job-{task_id}"}
        if method == "GET" and path.startswith("/jobs/"):
            return {"id": path.rsplit("/", 1)[-1], "status": "succeeded"}
        raise AssertionError((method, path, kwargs))


def test_redaction_removes_keys_bearer_url_credentials_and_explicit_secret() -> None:
    secret = "DO-NOT-ECHO"
    rendered = redact(
        {"api_key": secret, "authorization": "Bearer abc.def", "url": "https://demo:pass@example.test/v1"},
        extra_secrets=[secret],
    )
    assert secret not in rendered
    assert "abc.def" not in rendered
    assert "pass@" not in rendered
    assert "***" in rendered


def test_safe_api_client_redacts_call_scoped_secret_echoed_by_http_error() -> None:
    ephemeral_secret = "synthetic-ephemeral-qwen-key"

    def echo_secret(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            text=f"model configuration rejected api_key={ephemeral_secret}",
        )

    api = object.__new__(SafeApiClient)
    api._secrets = ()
    api.client = httpx.Client(
        base_url="https://platform.example.test/api/v1",
        transport=httpx.MockTransport(echo_secret),
    )
    try:
        with pytest.raises(DemoError) as error:
            api.call(
                "POST",
                "/model-configs",
                json={"api_key": ephemeral_secret},
                _redact_secrets=(ephemeral_secret,),
            )
    finally:
        api.close()

    assert ephemeral_secret not in str(error.value)
    assert "api_key=***" in str(error.value)


def test_api_url_rejects_credentials_and_non_http() -> None:
    assert safe_public_url("http://127.0.0.1:8080/api/v1/") == "http://127.0.0.1:8080/api/v1"
    with pytest.raises(DemoError, match="不能包含"):
        safe_public_url("https://demo:secret@example.test/api")
    with pytest.raises(DemoError, match="HTTP"):
        safe_public_url("file:///tmp/platform")


def test_prepare_plan_and_document_discovery_are_explicit_allowlists(tmp_path: Path) -> None:
    allowed = tmp_path / "集团本部采购实施细则（2025演示现行版）.md"
    allowed.write_text(DEMO_NOTICE, encoding="utf-8")
    (tmp_path / "demo_ground_truth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "private-secret.txt").write_text("secret", encoding="utf-8")
    assert discover_demo_documents(tmp_path) == [allowed]
    plan = build_prepare_plan(tmp_path)
    targets = {item.target for item in plan}
    assert allowed.name in targets
    assert "demo_ground_truth.json" not in targets
    assert "private-secret.txt" not in targets


def test_changed_allowlisted_demo_file_uploads_one_new_version_then_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filename = "扫描采购审批单（演示版）.jpg"
    content = b"deterministic-new-procurement-approval-scan"
    (tmp_path / filename).write_bytes(content)
    monkeypatch.setattr("scripts.demo.guolian_demo.DEMO_DOCUMENT_NAMES", (filename,))
    monkeypatch.setattr(
        "scripts.demo.guolian_demo.missing_required_demo_documents",
        lambda _root: [],
    )
    api = ChangedDemoDocumentApi(title=filename, old_sha256="0" * 64)
    preparer = DemoPreparer(api, fixture_root=tmp_path, poll_seconds=0)
    monkeypatch.setattr(preparer, "wait_job", lambda _job_id: {"status": "succeeded"})
    monkeypatch.setattr(
        preparer,
        "wait_knowledge_job",
        lambda _version_id: {"status": "succeeded"},
    )

    first = preparer.ensure_documents({"id": "space-demo"}, {"id": "media-demo"})
    second = preparer.ensure_documents({"id": "space-demo"}, {"id": "media-demo"})

    assert first == [
        {
            "title": filename,
            "status": "new-version",
            "document_id": "document-approval",
            "version_id": "version-2",
        }
    ]
    assert second[0]["status"] == "existing"
    assert len(api.uploads) == 1
    assert api.uploads[0]["content"] == content
    assert api.uploads[0]["data"] == {
        "space_id": "space-demo",
        "knowledge_processing_mode": "both",
        "document_id": "document-approval",
        "media_policy_id": "media-demo",
        "cloud_processing_confirmed": "true",
        "frame_budget_confirmed": "true",
    }
    assert api.versions[0]["sha256"] == hashlib.sha256(content).hexdigest()


def test_space_creation_is_idempotent_and_never_deletes() -> None:
    api = SpaceApi()
    first = DemoPreparer(api).ensure_space()
    second = DemoPreparer(api).ensure_space()
    assert first["id"] == second["id"] == "space-demo"
    assert len(api.spaces) == 1
    assert [call[0] for call in api.calls].count("POST") == 1
    assert [call[0] for call in api.calls].count("PUT") == 1
    assert all(call[0] != "DELETE" for call in api.calls)
    assert DEMO_NOTICE in api.spaces[0]["description"]


def test_demo_user_password_policy_is_checked_before_api_mutation() -> None:
    api = SpaceApi()
    with pytest.raises(DemoError, match="至少需要 10"):
        DemoPreparer(api, user_password="short")
    assert api.calls == []


def test_demo_user_and_qwen_document_placeholders_fail_before_api_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = SpaceApi()
    with pytest.raises(DemoError, match="占位值"):
        DemoPreparer(api, user_password="REPLACE_WITH_DEMO_USER_SECRET")
    assert api.calls == []

    monkeypatch.setenv("GUOLIAN_DEMO_QWEN_API_KEY", "REPLACE_WITH_DASHSCOPE_API_KEY")
    with pytest.raises(DemoError, match="占位值"):
        DemoPreparer(ModelReadinessApi()).ensure_model_readiness()


def test_demo_model_selection_requires_a_real_successful_connection_test() -> None:
    preparer = DemoPreparer(SpaceApi())
    rows = [
        {
            "id": "failed-default", "model_kind": "vision", "enabled": True,
            "is_default": True, "last_test_status": "failed",
        },
        {
            "id": "untested", "model_kind": "vision", "enabled": True,
            "is_default": False, "last_test_status": None,
        },
    ]
    assert preparer._model(rows, "vision") is None
    rows.append(
        {
            "id": "tested", "model_kind": "vision", "enabled": True,
            "is_default": False, "last_test_status": "success",
        }
    )
    assert preparer._model(rows, "vision")["id"] == "tested"


def test_demo_database_defaults_use_the_isolated_guolian_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = DemoPreparer(SpaceApi())
    postgresql = preparer._database_config("postgresql")
    mysql = preparer._database_config("mysql")
    assert postgresql["host"] == "guolian-demo-postgres"
    assert postgresql["port"] == 5432
    assert postgresql["database"] == postgresql["username"] == "guolian_demo"
    assert mysql["host"] == "guolian-demo-mysql"
    assert mysql["port"] == 3306
    assert mysql["database"] == mysql["username"] == "guolian_demo"

    monkeypatch.setenv("GUOLIAN_DEMO_POSTGRES_DATABASE", "custom_pg")
    monkeypatch.setenv("GUOLIAN_DEMO_MYSQL_DATABASE", "custom_mysql")
    monkeypatch.setenv("GUOLIAN_DEMO_POSTGRES_PUBLISHED_PORT", "65432")
    monkeypatch.setenv("GUOLIAN_DEMO_MYSQL_PUBLISHED_PORT", "63306")
    assert preparer._database_config("postgresql")["database"] == "custom_pg"
    assert preparer._database_config("mysql")["database"] == "custom_mysql"
    assert preparer._database_config("postgresql")["port"] == 5432
    assert preparer._database_config("mysql")["port"] == 3306


def test_account_free_demo_sources_are_real_idempotent_and_secret_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    names = (
        "智慧流程中枢项目例会要点（演示版）.txt",
        "知识加工策略（演示版）.yaml",
        "procurement_risk_rules.py",
        "项目系统依赖关系（演示版）.json",
        "供应商风险事件（演示版）.jsonl",
        "采购工作指引（演示版）.html",
    )
    for name in names:
        (fixture_root / name).write_text(f"{name}\n{DEMO_NOTICE}\n", encoding="utf-8")
    mount_root = tmp_path / "mounted-sources"
    local_root = mount_root / "guolian-enterprise-demo"
    monkeypatch.setenv("GUOLIAN_DEMO_SOURCE_MOUNT_ROOT", str(mount_root))
    monkeypatch.setenv("GUOLIAN_DEMO_LOCAL_SOURCE_ROOT", str(local_root))
    fake_minio = FakeMinio()
    monkeypatch.setattr("minio.Minio", lambda *_args, **_kwargs: fake_minio)

    api = SourcePreparationApi()
    preparer = DemoPreparer(
        api,
        fixture_root=fixture_root,
        object_store_endpoint="minio:9000",
        object_store_access_key="demo-access",
        object_store_secret="DO-NOT-REPORT",
        poll_seconds=0,
    )
    first = preparer.ensure_account_free_sources({"id": "space-demo"})
    second = preparer.ensure_account_free_sources({"id": "space-demo"})

    assert {row["source_type"] for row in first} == {
        "web", "rest", "rss", "sitemap", "git", "s3", "local_dir",
    }
    assert {row["sync"] for row in first} == {"synchronized"}
    assert {row["sync"] for row in second} == {"unchanged"}
    assert len(api.sources) == len(api.documents) == 7
    assert all(document.get("tags") for document in api.documents)
    assert len(list(local_root.iterdir())) == 3
    assert len(fake_minio.objects) == 3
    serialized_report = json.dumps(preparer.report.as_dict(), ensure_ascii=False)
    assert "DO-NOT-REPORT" not in serialized_report
    test_calls = [call for call in api.calls if call[0:2] == ("POST", "/sources/test")]
    assert test_calls and all("secret" not in call[2]["json"] for call in test_calls)


def test_account_free_sources_fail_before_mutation_without_minio_credentials(tmp_path: Path) -> None:
    api = SourcePreparationApi()
    with pytest.raises(DemoError, match="MINIO"):
        DemoPreparer(api, fixture_root=tmp_path).ensure_account_free_sources({"id": "space-demo"})
    assert api.calls == []


@pytest.mark.parametrize("unchanged", [False, True])
def test_source_pipeline_repairs_partial_knowledge_for_changed_and_unchanged_syncs(
    unchanged: bool,
) -> None:
    api = PartialSourcePipelineApi(unchanged=unchanged, retry_outcomes=["succeeded"])
    preparer = DemoPreparer(api, poll_seconds=0)

    result = preparer._wait_source_pipeline({"id": "sync-partial"})

    assert result["knowledge_status"] == "published"
    assert result["knowledge_reprocessed"] is True
    assert api.process_calls == 1


def test_source_pipeline_fails_closed_after_two_partial_knowledge_retries() -> None:
    api = PartialSourcePipelineApi(
        unchanged=True,
        retry_outcomes=["failed", "failed"],
    )
    preparer = DemoPreparer(api, poll_seconds=0)

    with pytest.raises(DemoError, match="两次真实重试后仍未完整发布"):
        preparer._wait_source_pipeline({"id": "sync-partial"})

    assert api.process_calls == 2


def test_paginated_items_and_graph_idempotency_load_records_after_first_500() -> None:
    api = GraphPaginationApi()
    rows = paginated_items(api, "/knowledge/entities?space_id=space-demo")
    assert len(rows) == 518
    graph = DemoPreparer(api).ensure_graph({"id": "space-demo"})
    assert graph == {
        "entities": 12,
        "facts": 9,
        "created": 0,
        "evidence_repaired": 0,
        "legacy_reverse_suppressed": 0,
    }
    entity_offsets = [
        parse_qs(urlsplit(path).query).get("offset", ["0"])[0]
        for method, path, _kwargs in api.calls
        if method == "GET" and path.startswith("/knowledge/entities?")
    ]
    fact_offsets = [
        parse_qs(urlsplit(path).query).get("offset", ["0"])[0]
        for method, path, _kwargs in api.calls
        if method == "GET" and path.startswith("/knowledge/facts?")
    ]
    assert "500" in entity_offsets
    assert "500" in fact_offsets
    fact_paths = [
        path for method, path, _kwargs in api.calls
        if method == "GET" and path.startswith("/knowledge/facts?")
    ]
    assert fact_paths and all("include_inferred=false" in path for path in fact_paths)
    nexus = next(row for row in api.entities if row.get("entity_type") == "产品")
    assert nexus["canonical_name"] == "NexusOne"
    assert not any(
        normalized_entity_name(row.get("canonical_name")) == "nexusone"
        and row.get("entity_type") == "其他"
        for row in api.entities
    )
    assert any(
        method == "PUT"
        and path == f"/knowledge/entities/{nexus['id']}"
        and kwargs["json"]["reason_note"] == "演示空间业务名称规范化"
        for method, path, kwargs in api.calls
    )


def test_graph_entity_conflict_reloads_all_pages_and_reuses_exact_entity() -> None:
    api = GraphPaginationApi(conflict_name="NexusOne")
    result = DemoPreparer(api).ensure_graph({"id": "space-demo"})
    assert result["created"] == 0
    assert api.conflict_raised is True
    nexus = [
        row for row in api.entities
        if normalized_entity_name(row["canonical_name"]) == "nexusone"
        and row["entity_type"] == "产品"
    ]
    assert len(nexus) == 1


def test_graph_seed_fails_closed_when_declared_evidence_terms_do_not_match() -> None:
    api = GraphPaginationApi(broken_evidence=True)

    with pytest.raises(DemoError, match="未找到同时匹配文档、结构位置和全部证据词"):
        DemoPreparer(api).ensure_graph({"id": "space-demo"})

    assert not any(
        method in {"POST", "PUT"}
        and (
            path.startswith("/knowledge/facts")
            or path.startswith("/knowledge/entities")
            or path.startswith("/curation/entities")
        )
        for method, path, _kwargs in api.calls
    )


def test_graph_seed_repairs_stale_evidence_and_soft_suppresses_legacy_direction() -> None:
    api = GraphPaginationApi(stale_evidence=True, legacy_reverse=True)

    result = DemoPreparer(api, poll_seconds=0).ensure_graph({"id": "space-demo"})

    assert result["evidence_repaired"] == 1
    assert result["legacy_reverse_suppressed"] == 1
    repaired = next(row for row in api.facts if row["id"] == "fact-0")
    legacy = next(row for row in api.facts if row["id"] == "legacy-reverse-responsibility")
    assert repaired["source_chunk_id"] == "chunk-policy"
    assert legacy["status"] == "suppressed"
    assert any(
        method == "PUT"
        and path == "/knowledge/facts/fact-0"
        and kwargs["json"]["reason_note"] == "演示 Ground Truth 证据对齐：FACT-DEMO-001"
        for method, path, kwargs in api.calls
    )
    assert any(
        method == "PUT"
        and path == "/knowledge/facts/legacy-reverse-responsibility"
        and "关系方向纠正" in kwargs["json"]["reason_note"]
        for method, path, kwargs in api.calls
    )


def test_model_preflight_uses_live_tests_and_repairs_stale_explicit_extraction() -> None:
    api = ModelReadinessApi()
    preparer = DemoPreparer(api)
    result = preparer.ensure_model_readiness()
    assert len(result["tested"]) == 4
    assert all(item["status"] == "success" for item in result["tested"].values())
    assert api.route_updates[0]["routes"]["agent_chat"] == "qwen"
    assert api.route_updates[0]["routes"]["vision_understanding"] == "vision"
    assert api.extraction_updates == [{"model_config_id": "qwen"}]
    assert preparer.report.resources["model_preflight"] == {
        "tested": 4,
        "successful": 4,
        "qwen_ready": True,
        "embedding_ready": True,
        "vision_ready": True,
        "asr_ready": True,
        "reranker_ready": False,
    }


def test_clean_demo_can_provision_qwen_from_ephemeral_environment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GUOLIAN_DEMO_QWEN_API_KEY", "synthetic-test-secret")
    api = CleanInstallModelReadinessApi()

    result = DemoPreparer(api).ensure_model_readiness()

    assert api.secret_seen is True
    qwen = next(row for row in api.models if row["id"] == "qwen")
    vision = next(row for row in api.models if row["id"] == "qwen-vision")
    assert "api_key" not in qwen
    assert qwen["is_default"] is True
    assert vision["is_default"] is True
    assert vision["config"]["credential_model_config_id"] == "qwen"
    assert result["tested"]["qwen"]["status"] == "success"
    assert result["tested"]["qwen-vision"]["status"] == "success"
    assert api.route_updates[-1]["routes"]["agent_chat"] == "qwen"
    assert api.route_updates[-1]["routes"]["vision_understanding"] == "qwen-vision"


def test_ephemeral_qwen_secret_never_rotates_an_existing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GUOLIAN_DEMO_QWEN_API_KEY", "intentionally-invalid-candidate")
    api = ModelReadinessApi()

    result = DemoPreparer(api).ensure_model_readiness()

    assert result["tested"]["qwen"]["status"] == "success"
    assert result["tested"]["vision"]["status"] == "success"
    assert next(row for row in api.models if row["id"] == "qwen")["is_default"] is True
    assert next(row for row in api.models if row["id"] == "vision")["is_default"] is True


def test_clean_demo_creates_a_crud_managed_default_routing_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyRoutingApi(CleanInstallModelReadinessApi):
        def __init__(self) -> None:
            super().__init__()
            self.policies: list[dict[str, Any]] = []

        def call(self, method: str, path: str, **kwargs: Any) -> Any:
            if method == "GET" and path == "/model-routing-policies":
                return [dict(row) for row in self.policies]
            if method == "POST" and path == "/model-routing-policies":
                row = {"id": "demo-route", **dict(kwargs["json"])}
                self.policies.append(row)
                return dict(row)
            return super().call(method, path, **kwargs)

    monkeypatch.setenv("GUOLIAN_DEMO_QWEN_API_KEY", "synthetic-test-secret")
    api = EmptyRoutingApi()

    DemoPreparer(api).ensure_model_readiness()

    assert len(api.policies) == 1
    policy = api.policies[0]
    assert policy["name"] == DEMO_ROUTING_POLICY_NAME
    assert policy["enabled"] is True
    assert policy["is_default"] is True
    assert policy["routes"]["agent_chat"] == "qwen"
    assert policy["routes"]["vision_understanding"] == "qwen-vision"


def test_failed_clean_qwen_candidate_can_recover_on_next_explicit_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RetryableApi(CleanInstallModelReadinessApi):
        def __init__(self) -> None:
            super().__init__()
            self.qwen_credential_valid = False

        def call(self, method: str, path: str, **kwargs: Any) -> Any:
            if method in {"POST", "PUT"} and path == "/model-configs":
                self.qwen_credential_valid = kwargs["json"].get("api_key") == "valid-test-secret"
            if method == "PUT" and path.startswith("/model-configs/"):
                key = kwargs["json"].get("api_key")
                if key:
                    self.qwen_credential_valid = key == "valid-test-secret"
            if method == "POST" and path.startswith("/model-configs/") and path.endswith("/test"):
                model_id = path.split("/")[2]
                if model_id in {"qwen", "qwen-vision"}:
                    status_value = "success" if self.qwen_credential_valid else "failed"
                    row = next(item for item in self.models if item["id"] == model_id)
                    row["last_test_status"] = status_value
                    return {"status": status_value, "elapsed_ms": 12}
            return super().call(method, path, **kwargs)

    api = RetryableApi()
    monkeypatch.setenv("GUOLIAN_DEMO_QWEN_API_KEY", "invalid-test-secret")
    first = DemoPreparer(api).ensure_model_readiness()
    assert first["tested"]["qwen"]["status"] == "failed"
    assert next(row for row in api.models if row["id"] == "qwen")["enabled"] is False

    monkeypatch.setenv("GUOLIAN_DEMO_QWEN_API_KEY", "valid-test-secret")
    preparer = DemoPreparer(api)
    second = preparer.ensure_model_readiness()

    assert second["tested"]["qwen"]["status"] == "success"
    assert second["tested"]["qwen-vision"]["status"] == "success"
    assert next(row for row in api.models if row["id"] == "qwen")["enabled"] is True
    assert next(row for row in api.models if row["id"] == "qwen-vision")["enabled"] is True
    assert any(
        action["action"] == "configure-from-secret" and action["status"] == "reconfigured"
        for action in preparer.report.actions
    )


def test_qwen_remains_preferred_for_extraction_when_kimi_also_succeeds() -> None:
    api = ModelReadinessApi()
    api.models.append(
        {
            "id": "kimi", "name": "Kimi K3", "model_kind": "llm",
            "model_name": "kimi-k3", "provider": "kimi", "enabled": True,
            "is_default": False, "last_test_status": None,
        }
    )

    DemoPreparer(api).ensure_model_readiness()

    assert api.extraction_updates == [{"model_config_id": "qwen"}]
    assert api.route_updates[-1]["routes"]["semantic_extract"] == "qwen"


def test_media_policy_never_routes_to_enabled_but_failed_models() -> None:
    api = FailedMediaModelsApi()
    preparer = DemoPreparer(api)

    policy = preparer.ensure_media_policy({"id": "space-demo"})

    config = policy["config"]
    assert config["cloud_processing_allowed"] is False
    assert config["video"]["asr_enabled"] is False
    assert config["asr"] == {
        "enabled": False,
        "model_config_id": None,
        "language": "zh",
        "minimum_speech_seconds": 0.2,
        "silence_policy": "metadata_only",
    }
    assert config["vision"]["enabled"] is False
    assert config["vision"]["model_config_id"] is None
    assert any("ASR" in warning for warning in preparer.report.warnings)
    assert any("视觉模型" in warning for warning in preparer.report.warnings)


def test_existing_analysis_tasks_are_reexecuted_against_current_evidence() -> None:
    api = ExistingAnalysisTasksApi()
    result = DemoPreparer(api, poll_seconds=0).ensure_analysis({"id": "space-demo"})

    assert len(result) == 3
    assert {row["status"] for row in result} == {"verified-rerun"}
    assert all(str(row["run_id"]).startswith("new-run-") for row in result)
    run_calls = [
        call for call in api.calls
        if call[0] == "POST" and call[1].startswith("/analysis/tasks/")
    ]
    assert len(run_calls) == 3


def test_reset_requires_both_execute_and_exact_space_code() -> None:
    validate_reset_confirmation(None, execute=False)
    with pytest.raises(DemoError, match="--confirm"):
        validate_reset_confirmation(None, execute=True)
    with pytest.raises(DemoError, match="--confirm"):
        validate_reset_confirmation("another-space", execute=True)
    validate_reset_confirmation(SPACE_CODE, execute=True)


def test_reset_defaults_to_read_only_scope_plan() -> None:
    api = ResetApi()
    result = DemoResetter(api).reset(execute=False, confirm=None)
    assert result["status"] == "planned"
    assert result["space_id"] == "demo-space-id"
    assert api.deleted == []
    assert set(result["counts"]) >= {"documents", "sources", "active_jobs"}


def test_reset_refuses_while_demo_jobs_are_active() -> None:
    api = ResetApi(active_job=True)
    with pytest.raises(DemoError, match="运行中任务"):
        DemoResetter(api).reset(execute=True, confirm=SPACE_CODE)
    assert api.deleted == []


def test_reset_detects_inference_jobs_that_only_reference_a_run_id() -> None:
    api = ResetApi(active_inference=True)
    with pytest.raises(DemoError, match="运行中任务"):
        DemoResetter(api).reset(execute=True, confirm=SPACE_CODE)
    assert api.deleted == []


def test_reset_can_only_delete_the_exact_demo_space() -> None:
    api = ResetApi()
    result = DemoResetter(api).reset(execute=True, confirm=SPACE_CODE)
    assert result["status"] == "deleted"
    assert api.deleted == ["/spaces/demo-space-id"]
    assert all("other-space" not in path for path in api.deleted)


def test_report_serialization_does_not_contain_secret_field_names() -> None:
    api = SpaceApi()
    report = DemoPreparer(api).report
    report.add("test", "create", "resource", "ok", password="hidden", rows=2)
    encoded = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "hidden" not in encoded
    assert '"rows": 2' in encoded


def test_database_mapping_manifest_is_strict_semantic_ir_without_credentials() -> None:
    api = SpaceApi()
    preparer = DemoPreparer(api)
    term_codes = {
        "organization", "supplier", "product", "project", "purchase_order",
        "purchase_order_item", "approval_record", "procurement_target", "risk_event", "project_product",
        "code", "name", "risk_level", "date", "status", "decision", "amount",
        "belongs_to", "correspond_to", "involve",
    }
    terms = {code: {"id": f"term-{code}"} for code in term_codes}
    manifest = preparer._mapping_manifest(
        source_id="source-demo",
        schema_id="schema-demo",
        ontology_id="ontology-demo",
        terms=terms,
    )
    assert len(manifest["entities"]) == 10
    assert len(manifest["relationships"]) == 11
    assert len(manifest["derived_metrics"]) == 1
    assert len(manifest["record_sets"]) == 1
    amount = next(row for row in manifest["attributes"] if row["id"] == "order-amount")
    assert amount["default_aggregate"] == "sum"
    assert amount["required_filters"][0]["operator"] == "in"
    assert amount["required_filters"][0]["value"] == ["signed", "executing", "accepted"]
    assert amount["required_relationships"] == [{
        "relationship_id": "approval-order",
        "target_entity_id": "approval-record",
        "quantifier": "exists",
        "filters": [{"attribute_id": "approval-decision", "operator": "eq", "value": "approved"}],
        "description": "订单必须存在审批结论为 approved 的审批记录，使用 EXISTS 避免多条审批记录导致金额重复累加。",
    }]
    gross_amount = next(row for row in manifest["attributes"] if row["id"] == "order-gross-amount")
    assert gross_amount["default_aggregate"] == "sum"
    assert gross_amount["required_filters"] == [{
        "attribute_id": "order-status",
        "operator": "ne",
        "value": "cancelled",
    }]
    assert gross_amount["required_relationships"] == []
    assert "不等同于有效采购金额" in gross_amount["business_definition"]
    risk_level = next(row for row in manifest["attributes"] if row["id"] == "risk-event-level")
    assert risk_level["column_id"] == "public.risk_events.risk_level"
    assert risk_level["semantic_type"] == "string"
    project_product_active = next(
        row for row in manifest["attributes"] if row["id"] == "project-product-active"
    )
    assert project_product_active["column_id"] == "public.project_products.active"
    assert project_product_active["semantic_type"] == "boolean"
    executed_at = next(row for row in manifest["attributes"] if row["id"] == "order-executed-at")
    assert executed_at["semantic_type"] == "datetime"
    assert "已经执行" in executed_at["business_definition"]
    completion_rate = manifest["derived_metrics"][0]
    assert completion_rate["numerator_attribute_id"] == "order-amount"
    assert completion_rate["denominator_attribute_id"] == "target-amount"
    assert completion_rate["dimensions"][0]["numerator_relationship_id"] == "order-org"
    assert completion_rate["dimensions"][0]["denominator_relationship_id"] == "target-org"
    pending_orders = manifest["record_sets"][0]
    assert {item["attribute_id"] for item in pending_orders["filters"]} == {
        "order-status", "order-executed-at",
    }
    assert pending_orders["relationship_constraints"][0]["filters"][0]["value"] == "pending"
    risk_product = next(row for row in manifest["relationships"] if row["id"] == "risk-product")
    assert risk_product["from_entity_id"] == "risk-event"
    assert risk_product["to_entity_id"] == "product"
    assert "查询风险影响项目" in risk_product["description"]
    assert risk_product["predicates"] == [{
        "left": {
            "object_id": "public.risk_events",
            "column_id": "public.risk_events.affected_product_id",
        },
        "operator": "=",
        "right": {
            "object_id": "public.products",
            "column_id": "public.products.id",
        },
    }]
    path = find_semantic_relationship_path(
        manifest["relationships"], "risk-event", "project", max_depth=3
    )
    assert path is not None
    assert [step["relationship_id"] for step in path] == [
        "risk-product", "project-product-product", "project-product-project",
    ]
    assert [step["direction"] for step in path] == ["forward", "reverse", "forward"]
    project_product_relation = next(
        row for row in manifest["relationships"] if row["id"] == "project-product-product"
    )
    assert project_product_relation["required_filters"] == [{
        "attribute_id": "project-product-active", "operator": "eq", "value": True,
    }]
    assert find_semantic_relationship_path(
        manifest["relationships"], "risk-event", "project", max_depth=2
    ) is None
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "password" not in serialized.casefold()
    assert "api_key" not in serialized.casefold()


@pytest.mark.parametrize(
    ("question", "aliases"),
    [
        ("2026 年集团采购目标完成率是多少？", ["numerator", "denominator", "percent"]),
        ("各单位采购目标完成率分别是多少？", ["org_unit", "actual", "target", "percent"]),
        (
            "集团目标完成率的分子和分母分别是什么？",
            ["numerator_label", "numerator", "denominator_label", "denominator"],
        ),
        ("有多少笔订单已经执行但审批尚未完成？", ["count"]),
    ],
)
def test_governed_semantic_definitions_generate_strict_ir_without_model(
    question: str,
    aliases: list[str],
) -> None:
    term_codes = {
        "organization", "supplier", "product", "project", "purchase_order",
        "purchase_order_item", "approval_record", "procurement_target", "risk_event",
        "project_product", "code", "name", "risk_level", "date", "status", "decision",
        "amount", "belongs_to", "correspond_to", "involve",
    }
    manifest = DemoPreparer(SpaceApi())._mapping_manifest(
        source_id="source-demo", schema_id="schema-demo", ontology_id="ontology-demo",
        terms={code: {"id": f"term-{code}"} for code in term_codes},
    )
    version = SemanticMappingVersion(
        id="mapping-demo", tenant_id="tenant", space_id="space", source_id="source-demo",
        mapping_set_id="mapping-set", schema_version_id="schema-demo", schema_fingerprint="a" * 64,
        mapping_hash="b" * 64, version_number=1, manifest=manifest, status="active", created_by="user",
    )

    def model_must_not_run(_prompt: str):
        raise AssertionError("受管理派生指标和记录集不应依赖模型临场生成查询")

    plan, query_ir = generate_semantic_plan_ir(
        question, version, api_key="unused", model="fixture", base_url=None,
        generator=model_must_not_run,
    )

    assert [item.alias for item in query_ir.select] == aliases
    assert validate_ir(query_ir, plan, version)["ok"] is True
    serialized = json.dumps(query_ir.model_dump(), ensure_ascii=False)
    assert "SELECT " not in serialized.upper()
    assert "purchase_orders" not in serialized


def test_risk_affected_project_path_is_valid_strict_ir_and_compiles_from_semantic_ids() -> None:
    term_codes = {
        "organization", "supplier", "product", "project", "purchase_order",
        "purchase_order_item", "approval_record", "procurement_target", "risk_event", "project_product",
        "code", "name", "risk_level", "date", "status", "decision", "amount",
        "belongs_to", "correspond_to", "involve",
    }
    manifest = DemoPreparer(SpaceApi())._mapping_manifest(
        source_id="source-demo",
        schema_id="schema-demo",
        ontology_id="ontology-demo",
        terms={code: {"id": f"term-{code}"} for code in term_codes},
    )
    object_columns: dict[str, set[str]] = {}

    def add_column(object_id: str, column_id: str) -> None:
        object_columns.setdefault(object_id, set()).add(column_id)

    for entity in manifest["entities"]:
        for fragment in entity["fragments"]:
            for column_id in [
                *fragment.get("identity_column_ids", []),
                fragment.get("display_column_id"),
            ]:
                if column_id:
                    add_column(fragment["object_id"], column_id)
    for attribute in manifest["attributes"]:
        fragment = next(
            fragment
            for entity in manifest["entities"]
            for fragment in entity["fragments"]
            if fragment["id"] == attribute["fragment_id"]
        )
        add_column(fragment["object_id"], attribute["column_id"])
    for relationship in manifest["relationships"]:
        for predicate in relationship["predicates"]:
            add_column(predicate["left"]["object_id"], predicate["left"]["column_id"])
            add_column(predicate["right"]["object_id"], predicate["right"]["column_id"])
    catalog = {
        "objects": [
            {
                "id": object_id,
                "schema": object_id.split(".", 1)[0],
                "name": object_id.split(".", 1)[1],
                "columns": [
                    {"id": column_id, "name": column_id.rsplit(".", 1)[-1]}
                    for column_id in sorted(columns)
                ],
            }
            for object_id, columns in sorted(object_columns.items())
        ]
    }
    source = SourceConnector(
        id="source-demo", tenant_id="tenant", space_id="space", name="演示经营库",
        source_type="database", config={"dialect": "postgresql"},
    )
    schema = DataSourceSchemaVersion(
        id="schema-demo", tenant_id="tenant", space_id="space", source_id="source-demo",
        version_number=1, schema_fingerprint="a" * 64, status="current", catalog=catalog,
    )
    version = SemanticMappingVersion(
        id="mapping-demo", tenant_id="tenant", space_id="space", source_id="source-demo",
        mapping_set_id="mapping-set", schema_version_id="schema-demo",
        schema_fingerprint="a" * 64, mapping_hash="b" * 64, version_number=1,
        manifest=manifest, status="active", created_by="user",
    )
    plan = SemanticQueryPlan.model_validate({
        "original_question": "东方智造的高风险事件影响多少个项目？",
        "intent": "按受影响产品路径去重统计项目",
        "entity_ids": ["risk-event", "supplier", "product", "project-product", "project"],
        "relationship_ids": [
            "risk-supplier", "risk-product", "project-product-product", "project-product-project",
        ],
        "outputs": [{
            "position": 1, "label": "受影响项目数量", "kind": "metric",
            "attribute_ids": ["project-name"], "aggregate": "count",
        }],
        "filters": [
            {"attribute_id": "supplier-name", "operator": "eq", "value": "东方智造"},
            {"attribute_id": "risk-event-level", "operator": "eq", "value": "high"},
            {"attribute_id": "risk-date", "operator": "gte", "value": "2026-01-01"},
            {"attribute_id": "risk-date", "operator": "lt", "value": "2027-01-01"},
        ],
        "expected_cardinality": "single_value",
        "distinct": True,
    })

    def attribute(attribute_id: str, binding: str) -> dict[str, Any]:
        return {"kind": "attribute", "attribute_id": attribute_id, "binding": binding}

    def equals(attribute_id: str, binding: str, value: Any, operator: str = "=") -> dict[str, Any]:
        return {
            "kind": "binary", "operator": operator,
            "left": attribute(attribute_id, binding),
            "right": {"kind": "literal", "value": value},
        }

    query_ir = SemanticQueryIR.model_validate({
        "from_entity": {"binding": "risk", "entity_id": "risk-event"},
        "joins": [
            {"binding": "supplier", "entity_id": "supplier", "relationship_id": "risk-supplier", "from_binding": "risk"},
            {"binding": "product", "entity_id": "product", "relationship_id": "risk-product", "from_binding": "risk"},
            {"binding": "usage", "entity_id": "project-product", "relationship_id": "project-product-product", "from_binding": "product"},
            {"binding": "project", "entity_id": "project", "relationship_id": "project-product-project", "from_binding": "usage"},
        ],
        "select": [{
            "alias": "affected_project_count",
            "expression": {
                "kind": "aggregate", "function": "count", "distinct": True,
                "expression": attribute("project-name", "project"),
            },
        }],
        "where": {
            "kind": "logical", "operator": "and", "operands": [
                equals("supplier-name", "supplier", "东方智造"),
                equals("risk-event-level", "risk", "high"),
                equals("risk-date", "risk", "2026-01-01", ">="),
                equals("risk-date", "risk", "2027-01-01", "<"),
            ],
        },
    })

    plan, query_ir = apply_activated_metric_contracts(plan, query_ir, version)
    assert any(
        item.attribute_id == "project-product-active" and item.value is True
        for item in plan.filters
    )
    assert validate_ir(query_ir, plan, version)["ok"] is True
    compiled = compile_structured_query(source, version, schema, query_ir, max_rows=20)
    assert "public.risk_events.affected_product_id" in compiled.referenced_columns
    assert "public.project_products.active" in compiled.referenced_columns
    assert "东方智造" not in compiled.sql_template
    assert "high" not in compiled.sql_template
    assert "东方智造" in compiled.parameters.values()
    assert "high" in compiled.parameters.values()


def test_q10_governed_query_deterministically_prefers_risk_event_level_over_supplier_level() -> None:
    term_codes = {
        "organization", "supplier", "product", "project", "purchase_order",
        "purchase_order_item", "approval_record", "procurement_target", "risk_event", "project_product",
        "code", "name", "risk_level", "date", "status", "decision", "amount",
        "belongs_to", "correspond_to", "involve",
    }
    manifest = DemoPreparer(SpaceApi())._mapping_manifest(
        source_id="source-demo", schema_id="schema-demo", ontology_id="ontology-demo",
        terms={code: {"id": f"term-{code}"} for code in term_codes},
    )
    version = SemanticMappingVersion(
        id="mapping-demo", tenant_id="tenant", space_id="space", source_id="source-demo",
        mapping_set_id="mapping-set", schema_version_id="schema-demo", schema_fingerprint="a" * 64,
        mapping_hash="b" * 64, version_number=1, manifest=manifest, status="active", created_by="user",
    )
    raw_plan = {
        "original_question": "东方智造的高风险事件影响多少个项目？",
        "intent": "统计高风险事件影响项目数",
        "entity_ids": ["risk-event", "supplier", "product", "project-product", "project"],
        "relationship_ids": ["risk-supplier", "risk-product", "project-product-product", "project-product-project"],
        "outputs": [{"position": 1, "label": "项目数", "kind": "metric", "attribute_ids": ["project-name"], "aggregate": "count"}],
        "filters": [
            {"attribute_id": "supplier-name", "operator": "eq", "value": "东方智造"},
            {"attribute_id": "supplier-risk", "operator": "eq", "value": "high"},
        ],
        "expected_cardinality": "single_value", "distinct": True,
    }
    raw_ir = {
        "from_entity": {"binding": "risk", "entity_id": "risk-event"},
        "joins": [
            {"binding": "supplier", "entity_id": "supplier", "relationship_id": "risk-supplier", "from_binding": "risk"},
            {"binding": "product", "entity_id": "product", "relationship_id": "risk-product", "from_binding": "risk"},
            {"binding": "usage", "entity_id": "project-product", "relationship_id": "project-product-product", "from_binding": "product"},
            {"binding": "project", "entity_id": "project", "relationship_id": "project-product-project", "from_binding": "usage"},
        ],
        "select": [{"alias": "count", "expression": {"kind": "aggregate", "function": "count", "distinct": True, "expression": {"kind": "attribute", "attribute_id": "project-name", "binding": "project"}}}],
        "where": {"kind": "logical", "operator": "and", "operands": [
            {"kind": "binary", "operator": "=", "left": {"kind": "attribute", "attribute_id": "supplier-name", "binding": "supplier"}, "right": {"kind": "literal", "value": "东方智造"}},
            {"kind": "binary", "operator": "=", "left": {"kind": "attribute", "attribute_id": "supplier-risk", "binding": "supplier"}, "right": {"kind": "literal", "value": "high"}},
        ]},
    }
    calls = 0

    def generator(_prompt: str):
        nonlocal calls
        calls += 1
        return {"plan": raw_plan, "query_ir": raw_ir}

    plan, query_ir = generate_semantic_plan_ir(
        raw_plan["original_question"], version, api_key="unused", model="fixture", base_url=None,
        generator=generator,
    )

    # This business path is part of the activated mapping's SQL-free governed
    # query catalog.  It therefore does not ask a model to rediscover a join
    # path that an administrator has already validated.
    assert calls == 0
    assert "supplier-risk" not in {item.attribute_id for item in plan.filters}
    assert "risk-event-level" in {item.attribute_id for item in plan.filters}
    def attribute_bindings(value: Any) -> list[tuple[str, str]]:
        if isinstance(value, list):
            return [binding for child in value for binding in attribute_bindings(child)]
        if not isinstance(value, dict):
            return []
        own = [(value["attribute_id"], value["binding"])] if value.get("kind") == "attribute" else []
        return own + [
            binding for child in value.values() for binding in attribute_bindings(child)
        ]

    bindings = attribute_bindings(query_ir.where.model_dump())
    assert any(attribute_id == "risk-event-level" for attribute_id, _ in bindings)
    assert not any(attribute_id == "supplier-risk" for attribute_id, _ in bindings)
    assert validate_ir(query_ir, plan, version)["ok"] is True


def test_all_structured_ground_truth_questions_use_activated_governed_semantics() -> None:
    term_codes = {
        "organization", "supplier", "product", "project", "purchase_order",
        "purchase_order_item", "approval_record", "procurement_target", "risk_event",
        "project_product", "code", "name", "risk_level", "date", "status",
        "decision", "amount", "belongs_to", "correspond_to", "involve",
    }
    manifest = DemoPreparer(SpaceApi())._mapping_manifest(
        source_id="source-demo", schema_id="schema-demo", ontology_id="ontology-demo",
        terms={code: {"id": f"term-{code}"} for code in term_codes},
    )
    version = SemanticMappingVersion(
        id="mapping-demo", tenant_id="tenant", space_id="space", source_id="source-demo",
        mapping_set_id="mapping-set", schema_version_id="schema-demo", schema_fingerprint="a" * 64,
        mapping_hash="b" * 64, version_number=1, manifest=manifest, status="active", created_by="user",
    )
    fixture_path = Path(__file__).resolve().parents[2] / "demo/guolian/structured_query_ground_truth.json"
    questions = json.loads(fixture_path.read_text(encoding="utf-8"))["questions"]
    model_calls: list[str] = []

    def generator(prompt: str):
        model_calls.append(prompt)
        raise AssertionError("演示验收问题不应绕过激活映射重新猜测业务口径")

    generated = [
        generate_semantic_plan_ir(
            item["question"], version,
            api_key="unused", model="fixture", base_url=None, generator=generator,
        )
        for item in questions
    ]

    assert len(generated) == 20
    assert model_calls == []
