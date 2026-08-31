from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


TARGET_LABELS = {
    "document_profile": "文档画像",
    "content_element": "原始内容",
    "chunk": "检索片段",
    "entity": "知识实体",
    "fact": "知识关系",
    "entity_pair": "实体关联约束",
    "quality_issue": "质量问题",
}

FIELD_LABELS = {
    "summary": "摘要",
    "classification": "主题分类",
    "document_type": "文档类型",
    "tags": "标签",
    "keywords": "关键词",
    "main_objects": "主要对象",
    "time_range": "时间范围",
    "text": "内容",
    "boost": "召回优先级",
    "status": "状态",
    "canonical_name": "实体名称",
    "entity_type": "实体类型",
    "aliases": "别名",
    "properties": "属性",
    "confidence": "置信度",
    "subject_entity_id": "主体",
    "predicate": "关系",
    "object_entity_id": "客体",
    "object_value": "客体值",
    "valid_from": "生效时间",
    "valid_to": "失效时间",
    "link": "实体关联",
}

OPERATION_LABELS = {
    "accept": "接受自动结果",
    "override": "修正",
    "reject": "屏蔽",
    "suppress": "屏蔽",
    "restore": "恢复",
    "merge": "合并",
    "split": "拆分",
    "must_link": "必须合并",
    "cannot_link": "必须区分",
    "lock": "锁定",
    "unlock": "解除锁定",
    "resolve": "处理完成",
    "ignore": "忽略",
    "rollback": "回滚",
}

SCOPE_LABELS = {
    "version_only": "仅当前版本",
    "document_future": "当前及以后版本",
    "space": "整个知识空间",
}

CASE_TYPE_LABELS = {
    "quality_issue": "质量问题",
    "fact_conflict": "事实冲突",
}

SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}

BATCH_STATUS_LABELS = {
    "active": "等待发布",
    "staged": "等待发布",
    "publishing": "发布中",
    "published": "已生效",
    "partial": "部分失败",
    "publish_failed": "发布失败",
    "rolled_back": "已回滚",
}


def business_label(kind: str, value: str) -> str:
    dictionaries = {
        "target": TARGET_LABELS,
        "field": FIELD_LABELS,
        "operation": OPERATION_LABELS,
        "scope": SCOPE_LABELS,
        "case_type": CASE_TYPE_LABELS,
        "severity": SEVERITY_LABELS,
        "batch_status": BATCH_STATUS_LABELS,
    }
    return dictionaries.get(kind, {}).get(value, value or "—")


def curation_impacts(target_type: str, field_path: str | None = None) -> list[str]:
    if target_type == "document_profile":
        return ["文档画像", "知识服务"]
    if target_type == "content_element":
        return ["重新切片", "语义抽取", "知识图谱", "知识检索", "知识分析"]
    if target_type == "chunk":
        return ["全文检索", "向量检索", "问答引用"]
    if target_type in {"entity", "fact", "entity_pair"}:
        return ["知识图谱", "知识分析", "图谱检索", "智能问答"]
    return ["治理记录"]


def compact_value(value: Any, *, limit: int = 180) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        text = "、".join(compact_value(item, limit=60) for item in value) or "—"
    elif isinstance(value, dict):
        if set(value).issubset({"start", "end"}):
            text = f"{value.get('start') or '未设置'} 至 {value.get('end') or '未设置'}"
        elif {"left_name", "right_name"}.issubset(value):
            text = f"{value.get('left_name')} 与 {value.get('right_name')}"
        else:
            parts = [f"{FIELD_LABELS.get(str(key), key)}：{compact_value(item, limit=60)}" for key, item in value.items()]
            text = "；".join(parts) or "—"
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_fields(fields: Iterable[str]) -> str:
    labels = list(dict.fromkeys(business_label("field", field) for field in fields))
    if not labels:
        return "治理内容"
    if len(labels) <= 3:
        return "、".join(labels)
    return "、".join(labels[:3]) + f"等 {len(labels)} 项"

