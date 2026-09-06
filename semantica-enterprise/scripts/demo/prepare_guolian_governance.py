#!/usr/bin/env python3
"""Prepare and verify the Guolian human-curation demonstration.

This module deliberately works through the public platform REST API.  It is
scoped to the ``guolian-enterprise-demo`` space, never deletes documents, and
does not open PostgreSQL, MinIO, OpenSearch, Qdrant or FalkorDB directly.

The executable checks every requested business scenario.  Supported manual
operations are exercised once and rolled back so the customer demonstration
still starts from an unresolved state.  Unsupported business workflows are
reported as gaps instead of being represented by synthetic CurationCase rows.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (
    DEMO_NOTICE,
    DEMO_ROOT,
    SPACE_CODE,
    SPACE_NAME,
    ApiLike,
    DemoError,
    SafeApiClient,
    admin_credentials_from_environment,
    api_url_from_environment,
    normalized_entity_name,
    paginated_items,
    redact,
)


MARKER_PREFIX = "[guolian-governance-demo:"


@dataclass(frozen=True)
class ScenarioDefinition:
    key: str
    name: str
    support: str
    real_boundary: str


SCENARIOS: tuple[ScenarioDefinition, ...] = (
    ScenarioDefinition(
        "policy_versions",
        "制度版本冲突",
        "partial",
        "可核验画像中的版本、生效区间并人工标注；尚无跨文档制度谱系和当前版本切换工作流。",
    ),
    ScenarioDefinition(
        "duplicate_documents",
        "重复文档",
        "supported",
        "确定性扫描跨文档完全/近重复内容；业务人员可保留两个来源，或屏蔽副本知识供给并按批次回滚。",
    ),
    ScenarioDefinition(
        "organization_alias",
        "组织别名",
        "supported",
        "通过实体组合治理建立 must-link、重写关系投影，并可按批次回滚。",
    ),
    ScenarioDefinition(
        "supplier_name_conflict",
        "供应商名称冲突",
        "supported",
        "通过 cannot-link 约束防止未经确认的自动合并，并可按批次回滚。",
    ),
    ScenarioDefinition(
        "classification_correction",
        "分类修正",
        "supported",
        "通过文档画像覆盖修正分类与标签，保留自动值、人工值和批次历史。",
    ),
    ScenarioDefinition(
        "ocr_low_confidence",
        "OCR 低置信度",
        "supported",
        "自动质量画像形成真实待办；内容元素修正会重新加工和发布，随后可完整回滚。",
    ),
    ScenarioDefinition(
        "missing_metadata",
        "元数据缺失",
        "supported",
        "通过文档画像补充时间范围、主要对象和标签，并可回滚。",
    ),
    ScenarioDefinition(
        "missing_relation",
        "关系缺失",
        "supported",
        "通过知识关系 API 绑定当前版本真实 Chunk 作为证据并发布图谱。",
    ),
    ScenarioDefinition(
        "expired_knowledge",
        "过期知识",
        "partial",
        "画像能够识别时间范围和历史标签；尚无独立到期策略、到期待办及当前检索自动失效。",
    ),
    ScenarioDefinition(
        "sensitive_data",
        "敏感数据",
        "partial",
        "数据库预览具备服务端隐藏/脱敏；非结构化文档尚无统一 PII 发布拦截工作流。",
    ),
    ScenarioDefinition(
        "rollback",
        "治理回滚",
        "supported",
        "画像、内容、实体组合和关系治理均保存不可变决定并生成回滚批次。",
    ),
)


@dataclass
class ScenarioResult:
    key: str
    name: str
    support: str
    status: str
    evidence: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class GovernanceReport:
    space_id: str
    space_name: str
    demo_data_notice: str = DEMO_NOTICE
    scenarios: list[ScenarioResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for row in self.scenarios:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {
            "dataset": SPACE_CODE,
            "space_id": self.space_id,
            "space_name": self.space_name,
            "demo_data_notice": self.demo_data_notice,
            "summary": counts,
            "scenarios": [asdict(row) for row in self.scenarios],
        }


def marker(key: str) -> str:
    return f"{MARKER_PREFIX}{key}]"


def scenario_by_key(key: str) -> ScenarioDefinition:
    row = next((item for item in SCENARIOS if item.key == key), None)
    if row is None:
        raise KeyError(key)
    return row


def select_named_entity(
    rows: Sequence[Mapping[str, Any]],
    name: str,
    *,
    entity_type: str | None = None,
) -> dict[str, Any] | None:
    """Choose a stable, visible canonical entity without guessing aliases."""

    candidates = [
        dict(row)
        for row in rows
        if normalized_entity_name(row.get("normalized_name") or row.get("canonical_name"))
        == normalized_entity_name(name)
        and (entity_type is None or row.get("entity_type") == entity_type)
    ]
    candidates.sort(
        key=lambda row: (
            (row.get("properties") or {}).get("dataset") == SPACE_CODE,
            int(row.get("source_count") or 0),
            row.get("confidence") or 0,
            str(row.get("id") or ""),
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def profile_changes(
    profile: Mapping[str, Any], requested: Mapping[str, Any]
) -> dict[str, Any]:
    effective = profile.get("effective") if isinstance(profile.get("effective"), dict) else profile
    return {key: value for key, value in requested.items() if effective.get(key) != value}


class GovernanceDemoPreparer:
    def __init__(
        self,
        api: ApiLike,
        *,
        exercise: bool = True,
        wait_timeout_seconds: int = 1800,
        poll_seconds: float = 1.5,
    ) -> None:
        self.api = api
        self.exercise = exercise
        self.wait_timeout_seconds = wait_timeout_seconds
        self.poll_seconds = poll_seconds
        self._space: dict[str, Any] | None = None
        self._documents: dict[str, dict[str, Any]] | None = None

    def _load_space(self) -> dict[str, Any]:
        if self._space is not None:
            return self._space
        matches = [row for row in self.api.call("GET", "/spaces") if row.get("code") == SPACE_CODE]
        if len(matches) != 1:
            raise DemoError(f"只能治理唯一的演示空间 {SPACE_CODE}，当前找到 {len(matches)} 个")
        if matches[0].get("name") != SPACE_NAME:
            raise DemoError("演示空间编码存在，但名称与国联演示空间不一致")
        self._space = dict(matches[0])
        return self._space

    def _load_documents(self) -> dict[str, dict[str, Any]]:
        if self._documents is None:
            space_id = self._load_space()["id"]
            self._documents = {
                row["title"]: dict(row)
                for row in self.api.call("GET", f"/documents?space_id={space_id}")
            }
        return self._documents

    def _document_context(self, title: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        document = self._load_documents().get(title)
        if document is None:
            raise DemoError(f"治理演示文档不存在：{title}")
        detail = self.api.call("GET", f"/documents/{document['id']}")
        current_id = detail.get("current_version_id")
        version = next((row for row in detail.get("versions") or [] if row.get("id") == current_id), None)
        if not version:
            raise DemoError(f"治理演示文档没有当前版本：{title}")
        profile = self.api.call("GET", f"/versions/{version['id']}/profile")
        return detail, dict(version), dict(profile)

    def _wait_job(self, job: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not job or not job.get("id"):
            return None
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            current = self.api.call("GET", f"/jobs/{job['id']}")
            if current.get("status") == "succeeded":
                return current
            if current.get("status") in {"failed", "cancelled"}:
                raise DemoError(
                    f"治理任务未成功：{current.get('status')} / {current.get('error_code') or 'UNKNOWN'}"
                )
            time.sleep(self.poll_seconds)
        raise DemoError(f"治理任务 {job['id']} 超过 {self.wait_timeout_seconds} 秒")

    def _wait_document_published(self, document_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            detail = self.api.call("GET", f"/documents/{document_id}")
            current_id = detail.get("current_version_id")
            current = next((row for row in detail.get("versions") or [] if row.get("id") == current_id), None)
            summary = (current or {}).get("parse_summary") or {}
            if current and current.get("status") == "ready" and summary.get("knowledge_status") == "published":
                return detail
            if current and summary.get("knowledge_status") == "failed":
                raise DemoError("重复文档 Fixture 知识加工失败")
            time.sleep(self.poll_seconds)
        raise DemoError("重复文档 Fixture 知识加工超时")

    def _exercise_duplicate_documents(self) -> ScenarioResult:
        definition = scenario_by_key("duplicate_documents")
        space_id = self._load_space()["id"]
        original_title = "集团本部采购实施细则（2025演示现行版）.md"
        duplicate_title = "集团本部采购实施细则（邮件附件副本-演示）.md"
        documents = self._load_documents()
        duplicate = documents.get(duplicate_title)
        if duplicate is None and self.exercise:
            fixture = DEMO_ROOT / duplicate_title
            if not fixture.is_file():
                raise DemoError(f"重复文档 Fixture 不存在：{duplicate_title}")
            uploaded = self.api.call(
                "POST",
                "/documents/upload",
                data={"space_id": space_id, "knowledge_processing_mode": "vector"},
                files={"file": (duplicate_title, fixture.read_bytes(), "text/markdown")},
            )
            self._wait_job(uploaded.get("job"))
            self._wait_document_published(uploaded["document"]["id"])
            self.api.call(
                "PUT",
                f"/documents/{uploaded['document']['id']}",
                json={"tags": ["国联集团演示", "重复来源", "演示数据"]},
            )
            self._documents = None
            documents = self._load_documents()
            duplicate = documents.get(duplicate_title)
        if duplicate is None:
            return ScenarioResult(
                definition.key, definition.name, definition.support, "failed",
                ["确定性重复扫描、保留/合并和批次回滚接口已启用"],
                ["尚未上传重复来源 Fixture；请先运行完整治理准备脚本"],
            )
        if self.exercise:
            self.api.call("POST", f"/curation/duplicates/scan?space_id={space_id}")
        cases = paginated_items(
            self.api,
            f"/curation/cases?space_id={space_id}&case_type=duplicate_document",
        )
        candidate = None
        original = documents.get(original_title)
        for row in cases:
            detail = self.api.call("GET", f"/curation/cases/{row['id']}")
            pair_ids = {item.get("document_id") for item in detail.get("duplicate_documents") or []}
            if original and pair_ids == {original["id"], duplicate["id"]}:
                candidate = detail
                break
        if candidate is None:
            raise DemoError("跨文档扫描没有生成预期的完全重复候选")
        evidence = [
            f"真实候选类型：{(candidate.get('evidence') or {}).get('match_type')}",
            f"正文相似度：{float((candidate.get('evidence') or {}).get('similarity') or 0):.1%}",
        ]
        existing = self._marked_decisions("duplicate_documents")
        if existing:
            for batch_id in {row["batch_id"] for row in existing if row.get("batch_id")}:
                self._rollback_batch(batch_id)
            evidence.append("此前已真实合并知识供给并按批次回滚，幂等复用治理历史")
            return ScenarioResult(definition.key, definition.name, definition.support, "passed", evidence)
        if not self.exercise:
            evidence.append("重复来源比较与合并操作已就绪")
            return ScenarioResult(definition.key, definition.name, definition.support, "ready", evidence)
        pair = candidate.get("duplicate_documents") or []
        original_side = next((item.get("side") for item in pair if item.get("document_id") == original["id"]), None)
        action = "merge_into_left" if original_side == "left" else "merge_into_right"
        result = self.api.call(
            "POST",
            f"/curation/duplicates/{candidate['id']}/resolve",
            json={
                "action": action,
                "reason_note": f"{marker('duplicate_documents')} 保留制度主文档，将邮件附件副本退出当前知识供给；{DEMO_NOTICE}",
            },
        )
        self._wait_job(result.get("job"))
        resolved = self.api.call("GET", f"/curation/cases/{candidate['id']}")
        if resolved.get("status") != "handled" or (resolved.get("evidence") or {}).get("resolution") != "merged":
            raise DemoError("重复文档合并未形成可追溯治理结果")
        self._rollback_batch(result["batch"]["id"])
        restored = self.api.call("GET", f"/curation/cases/{candidate['id']}")
        if restored.get("status") != "open" or (restored.get("evidence") or {}).get("resolution") != "rolled_back":
            raise DemoError("重复文档治理回滚后没有重新打开待办")
        evidence.append("真实屏蔽副本 Chunk/Fact、发布新版本并完成批次回滚")
        return ScenarioResult(definition.key, definition.name, definition.support, "passed", evidence)

    def _decisions(self) -> list[dict[str, Any]]:
        space_id = self._load_space()["id"]
        return paginated_items(self.api, f"/curation/decisions?space_id={space_id}")

    def _marked_decisions(self, key: str) -> list[dict[str, Any]]:
        prefix = marker(key)
        return [row for row in self._decisions() if prefix in str(row.get("reason_note") or "")]

    def _rollback_batch(self, batch_id: str) -> None:
        batch = self.api.call("GET", f"/curation/batches/{batch_id}")
        if not batch.get("can_rollback"):
            return
        rolled = self.api.call("POST", f"/curation/batches/{batch_id}/rollback")
        self._wait_job(rolled.get("job"))

    def _exercise_profile(
        self,
        *,
        key: str,
        title: str,
        requested: Mapping[str, Any],
        reason: str,
    ) -> list[str]:
        existing = self._marked_decisions(key)
        if existing:
            for batch_id in {row["batch_id"] for row in existing if row.get("batch_id")}:
                self._rollback_batch(batch_id)
            return ["此前已通过真实画像修正与回滚，幂等复用治理历史"]
        _document, version, before = self._document_context(title)
        changes = profile_changes(before, requested)
        if not changes:
            return ["自动画像已具备目标业务元数据，无需人为制造错误"]
        if not self.exercise:
            return [f"已验证 {title} 可治理字段：{'、'.join(sorted(changes))}"]
        result = self.api.call(
            "POST",
            f"/curation/profiles/{version['id']}",
            json={
                "space_id": self._load_space()["id"],
                "changes": changes,
                "scope": "version_only",
                "reason_note": f"{marker(key)} {reason}；{DEMO_NOTICE}",
            },
        )
        batch_id = result["batch"]["id"]
        after = self.api.call("GET", f"/versions/{version['id']}/profile")
        for field, value in changes.items():
            if after.get(field) != value:
                raise DemoError(f"画像修正未生效：{title} / {field}")
        self._rollback_batch(batch_id)
        restored = self.api.call("GET", f"/versions/{version['id']}/profile")
        for field in changes:
            if restored.get(field) != before.get(field):
                raise DemoError(f"画像回滚未恢复原值：{title} / {field}")
        return [f"真实修正并回滚 {'、'.join(sorted(changes))}，保留审计批次"]

    def _exercise_entity_pair(
        self,
        *,
        key: str,
        left_name: str,
        right_name: str,
        operation: str,
        left_type: str | None,
        right_type: str | None,
        reason: str,
    ) -> list[str]:
        existing = self._marked_decisions(key)
        if existing:
            for batch_id in {row["batch_id"] for row in existing if row.get("batch_id")}:
                self._rollback_batch(batch_id)
            return ["此前已通过真实实体组合治理与回滚，幂等复用治理历史"]
        if not self.exercise:
            return [f"将验证 {left_name} 与 {right_name} 的 {operation} 约束"]
        space_id = self._load_space()["id"]
        entities = paginated_items(self.api, f"/knowledge/entities?space_id={space_id}")
        left = select_named_entity(entities, left_name, entity_type=left_type)
        right = select_named_entity(entities, right_name, entity_type=right_type)
        if not left or not right:
            raise DemoError(f"实体组合治理缺少节点：{left_name} / {right_name}")
        payload: dict[str, Any] = {
            "space_id": space_id,
            "left_entity_id": left["id"],
            "right_entity_id": right["id"],
            "operation": operation,
            "reason_note": f"{marker(key)} {reason}；{DEMO_NOTICE}",
        }
        if operation in {"merge", "must_link"}:
            payload["winner_entity_id"] = left["id"]
        result = self.api.call("POST", "/curation/entities/pair", json=payload)
        self._wait_job(result.get("job"))
        marked = self._marked_decisions(key)
        pair = next((row for row in marked if row.get("target_type") == "entity_pair"), None)
        expected = "must_link" if operation in {"merge", "must_link"} else "cannot_link"
        if not pair or pair.get("operation") != expected:
            raise DemoError(f"实体组合约束未生效：{left_name} / {right_name}")
        self._rollback_batch(result["batch"]["id"])
        visible = paginated_items(self.api, f"/knowledge/entities?space_id={space_id}")
        if not select_named_entity(visible, left_name, entity_type=left_type):
            raise DemoError(f"实体治理回滚后未恢复节点：{left_name}")
        if not select_named_entity(visible, right_name, entity_type=right_type):
            raise DemoError(f"实体治理回滚后未恢复节点：{right_name}")
        return [f"真实建立 {expected} 约束、发布图谱并按批次回滚"]

    def _ocr_case_and_exercise(self) -> ScenarioResult:
        definition = scenario_by_key("ocr_low_confidence")
        title = "供应商评估表截图（演示版）.png"
        document, version, profile = self._document_context(title)
        workbench = self.api.call(
            "GET", f"/curation/workbench?space_id={self._load_space()['id']}&status=all&limit=300"
        )
        cases = [
            row for row in workbench.get("items") or []
            if row.get("document_id") == document["id"]
            and any(term in str(row.get("title") or "") for term in ("OCR", "低置信度", "模糊"))
        ]
        evidence = [f"自动画像生成 {len(cases)} 条 OCR/低置信度治理待办"]
        if not cases:
            return ScenarioResult(
                definition.key, definition.name, definition.support, "failed", evidence,
                ["没有从真实自动画像生成 OCR 治理待办"],
            )
        existing = self._marked_decisions(definition.key)
        if existing:
            for batch_id in {row["batch_id"] for row in existing if row.get("batch_id")}:
                self._rollback_batch(batch_id)
            evidence.append("此前已通过真实 OCR 内容修正、重新加工与回滚")
            return ScenarioResult(definition.key, definition.name, definition.support, "passed", evidence)
        elements = self.api.call("GET", f"/versions/{version['id']}/elements?limit=500").get("items") or []
        element = next(
            (
                row for row in elements
                if row.get("element_type") == "image"
                and any(token in str(row.get("automatic_text") or row.get("text") or "") for token in ("评估日其", "B-0l"))
            ),
            None,
        )
        if not element:
            evidence.append("OCR 输出当前未包含预设可安全修正的误识别标记")
            return ScenarioResult(definition.key, definition.name, definition.support, "partial", evidence)
        original = str(element.get("automatic_text") or element.get("text") or "")
        corrected = original.replace("评估日其", "评估日期").replace("B-0l", "B-01")
        if not self.exercise:
            evidence.append("已定位真实 OCR 内容元素和可修正字符")
            return ScenarioResult(definition.key, definition.name, definition.support, "ready", evidence)
        created = self.api.call(
            "POST", "/curation/decisions",
            json={
                "space_id": self._load_space()["id"],
                "target_type": "content_element",
                "target_id": element["element_id"],
                "version_id": version["id"],
                "field_path": "text",
                "operation": "override",
                "value": corrected,
                "scope": "version_only",
                "reason_code": "ocr_human_confirmation",
                "reason_note": f"{marker(definition.key)} 人工核验 OCR 字符；{DEMO_NOTICE}",
                "auto_publish": True,
            },
        )
        self._wait_job(created.get("job"))
        current = self.api.call("GET", f"/versions/{version['id']}/elements?limit=500").get("items") or []
        projected = next((row for row in current if row.get("element_id") == element["element_id"]), None)
        if not projected or "评估日期" not in str(projected.get("text") or ""):
            raise DemoError("OCR 人工修正没有进入有效内容投影")
        self._rollback_batch(created["batch"]["id"])
        restored_rows = self.api.call("GET", f"/versions/{version['id']}/elements?limit=500").get("items") or []
        restored = next((row for row in restored_rows if row.get("element_id") == element["element_id"]), None)
        if not restored or restored.get("text") != restored.get("automatic_text"):
            raise DemoError("OCR 人工修正回滚后未恢复自动解析内容")
        evidence.append("真实修正 OCR 内容、重新切片/抽取/发布并完成回滚")
        evidence.append(f"原始媒体可信度：{profile.get('media_confidence')}")
        return ScenarioResult(definition.key, definition.name, definition.support, "passed", evidence)

    def _ensure_missing_relation(self) -> ScenarioResult:
        definition = scenario_by_key("missing_relation")
        space_id = self._load_space()["id"]
        entities = paginated_items(self.api, f"/knowledge/entities?space_id={space_id}")
        subject = select_named_entity(entities, "NexusOne", entity_type="产品")
        obj = select_named_entity(entities, "智慧流程中枢项目", entity_type="项目")
        if not subject or not obj:
            return ScenarioResult(
                definition.key, definition.name, definition.support, "blocked", [],
                ["缺少 NexusOne（产品）或智慧流程中枢项目（项目）实体"],
            )
        facts = paginated_items(
            self.api, f"/knowledge/facts?space_id={space_id}&include_inferred=false"
        )
        relation = next(
            (
                row for row in facts
                if row.get("subject_entity_id") == subject["id"]
                and row.get("predicate") == "用于"
                and row.get("object_entity_id") == obj["id"]
            ),
            None,
        )
        created = False
        if not relation and self.exercise:
            document, version, _profile = self._document_context("项目系统依赖关系（演示版）.json")
            chunks = self.api.call("GET", f"/versions/{version['id']}/chunks?limit=500").get("items") or []
            chunk = next(
                (
                    row for row in chunks
                    if "NexusOne" in str(row.get("text") or "")
                    and "智慧流程中枢" in str(row.get("text") or "")
                ),
                None,
            )
            if not chunk:
                return ScenarioResult(
                    definition.key, definition.name, definition.support, "blocked", [],
                    [f"{document['title']} 没有同时包含两端对象的当前 Chunk"],
                )
            try:
                relation = self.api.call(
                    "POST", "/knowledge/facts",
                    json={
                        "space_id": space_id,
                        "subject_entity_id": subject["id"],
                        "predicate": "用于",
                        "object_entity_id": obj["id"],
                        "source_chunk_id": chunk["id"],
                        "confidence": 1,
                        "status": "published",
                    },
                )
                created = True
            except DemoError as exc:
                if "HTTP 409" not in str(exc):
                    raise
                facts = paginated_items(
                    self.api, f"/knowledge/facts?space_id={space_id}&include_inferred=false"
                )
                relation = next(
                    (
                        row for row in facts
                        if row.get("subject_entity_id") == subject["id"]
                        and row.get("predicate") == "用于"
                        and row.get("object_entity_id") == obj["id"]
                    ),
                    None,
                )
        if relation is None:
            return ScenarioResult(
                definition.key, definition.name, definition.support, "ready",
                ["两端实体和真实来源片段均已准备，执行模式下可补充关系"],
            )
        if not relation.get("source_chunk_id"):
            return ScenarioResult(
                definition.key, definition.name, definition.support, "partial",
                ["关系已存在并进入图谱"], ["现有关系缺少可打开的来源 Chunk"],
            )
        return ScenarioResult(
            definition.key, definition.name, definition.support, "passed",
            [
                "NexusOne —用于→ 智慧流程中枢项目 已发布",
                "关系绑定当前文档版本的真实 Chunk",
                "本次创建" if created else "幂等复用已有关系",
            ],
        )

    def _sensitive_data(self) -> ScenarioResult:
        definition = scenario_by_key("sensitive_data")
        space_id = self._load_space()["id"]
        sources = [
            row for row in self.api.call("GET", "/sources")
            if row.get("space_id") == space_id and row.get("source_type") == "database"
        ]
        postgres = next((row for row in sources if row.get("name") == "演示·国联经营数据 PostgreSQL"), None)
        if not postgres:
            return ScenarioResult(definition.key, definition.name, definition.support, "blocked", [], ["演示 PostgreSQL 数据源不存在"])
        objects = self.api.call("GET", f"/sources/{postgres['id']}/data-objects").get("objects") or []
        contacts = next((row for row in objects if row.get("name") == "supplier_contacts"), None)
        if contacts is None:
            return ScenarioResult(
                definition.key, definition.name, definition.support, "gap", [],
                ["数据库 Fixture 含 supplier_contacts，但当前数据源表白名单未接入，无法现场验证手机、邮箱和 Token 脱敏"],
            )
        columns = {row.get("name"): row for row in contacts.get("columns") or []}
        order = columns.get("id")
        preview = self.api.call(
            "POST", f"/sources/{postgres['id']}/data-preview",
            json={
                "object_id": contacts["id"], "mode": "live", "page": 1, "page_size": 5,
                "order_by": order.get("id") if order else None,
                "order_direction": "asc", "filters": [],
            },
        )
        visible_names = {row.get("name") for row in preview.get("columns") or []}
        rendered = json.dumps(preview.get("rows") or [], ensure_ascii=False)
        if "demo_api_token" in visible_names or "never-return" in rendered:
            return ScenarioResult(definition.key, definition.name, definition.support, "failed", [], ["禁止字段或明文 Token 出现在预览响应中"])
        if not any("****" in str(row.get("demo_mobile") or "") for row in preview.get("rows") or []):
            return ScenarioResult(definition.key, definition.name, definition.support, "failed", [], ["手机号没有在服务端脱敏"])
        return ScenarioResult(
            definition.key, definition.name, definition.support, "passed",
            ["demo_api_token 未返回浏览器", "手机号由服务端脱敏", "预览通过当前知识空间数据源权限"],
            ["非结构化文档统一 PII 发布拦截仍未实现"],
        )

    def _quality_cases(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        space_id = self._load_space()["id"]
        workbench = self.api.call(
            "GET", f"/curation/workbench?space_id={space_id}&status=all&limit=300"
        )
        return workbench, list(workbench.get("items") or [])

    def run(self) -> GovernanceReport:
        space = self._load_space()
        report = GovernanceReport(space_id=space["id"], space_name=space["name"])
        workbench, cases = self._quality_cases()

        old_doc, _old_version, old_profile = self._document_context("集团本部采购实施细则（2023演示旧版）.md")
        new_doc, _new_version, new_profile = self._document_context("集团本部采购实施细则（2025演示现行版）.md")
        old_range = old_profile.get("time_range") or {}
        new_range = new_profile.get("time_range") or {}
        version_ok = bool(
            old_range.get("end") and new_range.get("start")
            and old_range["end"] < new_range["start"]
            and "历史版本" in (old_profile.get("tags") or [])
        )
        report.scenarios.append(ScenarioResult(
            "policy_versions", "制度版本冲突", "partial", "passed" if version_ok else "failed",
            [
                f"旧版：{old_doc['title']}，有效至 {old_range.get('end') or '未识别'}",
                f"新版：{new_doc['title']}，生效于 {new_range.get('start') or '未识别'}",
            ],
            ["两份文件仍是两个 Document，当前没有制度谱系、版本对比和按生效期切换检索的独立后端闭环"],
        ))

        try:
            report.scenarios.append(self._exercise_duplicate_documents())
        except DemoError as exc:
            report.scenarios.append(ScenarioResult(
                "duplicate_documents", "重复文档", "supported", "failed", [], [str(exc)]
            ))

        try:
            evidence = self._exercise_entity_pair(
                key="organization_alias", left_name="数字科技公司", right_name="国联数科",
                operation="merge", left_type="组织", right_type="组织",
                reason="验证组织别名合并、关系重写和可回滚",
            )
            report.scenarios.append(ScenarioResult("organization_alias", "组织别名", "supported", "passed" if self.exercise else "ready", evidence))
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("organization_alias", "组织别名", "supported", "failed", [], [str(exc)]))

        try:
            evidence = self._exercise_entity_pair(
                key="supplier_name_conflict", left_name="东方智造", right_name="东方智造有限公司",
                operation="cannot_link", left_type="供应商", right_type=None,
                reason="数据库统一编码尚未人工确认，先约束两个供应商名称不得自动合并",
            )
            report.scenarios.append(ScenarioResult("supplier_name_conflict", "供应商名称冲突", "supported", "passed" if self.exercise else "ready", evidence))
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("supplier_name_conflict", "供应商名称冲突", "supported", "failed", [], [str(exc)]))

        _weekly, _weekly_version, weekly_profile = self._document_context("智慧流程中枢项目周报（演示版）.docx")
        requested_tags = list(dict.fromkeys([*(weekly_profile.get("tags") or []), "人工复核"]))
        try:
            evidence = self._exercise_profile(
                key="classification_correction", title="智慧流程中枢项目周报（演示版）.docx",
                requested={"classification": "项目周报", "tags": requested_tags},
                reason="将通用项目材料细化为项目周报",
            )
            report.scenarios.append(ScenarioResult("classification_correction", "分类修正", "supported", "passed" if self.exercise else "ready", evidence))
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("classification_correction", "分类修正", "supported", "failed", [], [str(exc)]))

        try:
            report.scenarios.append(self._ocr_case_and_exercise())
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("ocr_low_confidence", "OCR 低置信度", "supported", "failed", [], [str(exc)]))

        _guide, _guide_version, guide_profile = self._document_context("采购工作指引（演示版）.html")
        guide_objects = list(dict.fromkeys([*(guide_profile.get("main_objects") or []), "集团采购管理部"]))
        try:
            evidence = self._exercise_profile(
                key="missing_metadata", title="采购工作指引（演示版）.html",
                requested={
                    "time_range": {"start": "2025-01-01", "end": "2026-12-31"},
                    "main_objects": guide_objects,
                },
                reason="补充演示指引的适用时间和责任对象",
            )
            missing_cases = [row for row in cases if row.get("document_id") == _guide["id"] and any(term in str(row.get("title") or "") for term in ("日期", "时间", "缺失"))]
            evidence.append(f"自动画像相关缺失提示：{len(missing_cases)} 条")
            report.scenarios.append(ScenarioResult("missing_metadata", "元数据缺失", "supported", "passed" if self.exercise else "ready", evidence))
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("missing_metadata", "元数据缺失", "supported", "failed", [], [str(exc)]))

        try:
            report.scenarios.append(self._ensure_missing_relation())
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("missing_relation", "关系缺失", "supported", "failed", [], [str(exc)]))

        expired_ok = bool(old_range.get("end") and old_range["end"] < "2026-01-01")
        report.scenarios.append(ScenarioResult(
            "expired_knowledge", "过期知识", "partial", "passed" if expired_ok else "failed",
            [f"历史制度有效期结束：{old_range.get('end') or '未识别'}", "自动画像保留历史版本标签"],
            ["尚无独立过期策略命中、继续有效/失效操作和检索投影自动排除闭环"],
        ))
        try:
            report.scenarios.append(self._sensitive_data())
        except DemoError as exc:
            report.scenarios.append(ScenarioResult("sensitive_data", "敏感数据", "partial", "failed", [], [str(exc)]))

        marked = [row for row in self._decisions() if MARKER_PREFIX in str(row.get("reason_note") or "")]
        rolled_back = [row for row in marked if row.get("status") == "rolled_back"]
        rollback_ok = bool(rolled_back)
        report.scenarios.append(ScenarioResult(
            "rollback", "治理回滚", "supported", "passed" if rollback_ok else "ready",
            [
                f"演示治理决定：{len(marked)} 条",
                f"已回滚决定：{len(rolled_back)} 条",
                f"工作台待办：{workbench.get('case_total', 0)} 条",
            ],
            [] if rollback_ok else ["尚未执行可回滚治理操作"],
        ))
        return report


def build_dry_run() -> dict[str, Any]:
    return {
        "status": "dry-run",
        "dataset": SPACE_CODE,
        "space_name": SPACE_NAME,
        "demo_data_notice": DEMO_NOTICE,
        "destructive": False,
        "operations_are_rolled_back": True,
        "scenarios": [asdict(row) for row in SCENARIOS],
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="准备并验证国联集团演示空间的真实人工治理能力")
    value.add_argument("--dry-run", action="store_true", help="只输出治理能力边界和计划")
    value.add_argument("--verify-only", action="store_true", help="只读验证，不执行治理与回滚")
    value.add_argument("--wait-timeout", type=int, default=1800, help="治理后台任务最大等待秒数")
    value.add_argument("--compact", action="store_true", help="输出单行 JSON")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.dry_run:
            result = build_dry_run()
        else:
            username, password = admin_credentials_from_environment()
            with SafeApiClient(
                base_url=api_url_from_environment(), username=username, password=password,
                timeout_seconds=max(120, args.wait_timeout),
            ) as api:
                report = GovernanceDemoPreparer(
                    api, exercise=not args.verify_only,
                    wait_timeout_seconds=args.wait_timeout,
                ).run()
                result = {"status": "completed", **report.as_dict()}
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        failed = [
            row for row in result.get("scenarios", [])
            if row.get("status") == "failed"
        ]
        return 2 if failed else 0
    except Exception as exc:
        print(redact({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
