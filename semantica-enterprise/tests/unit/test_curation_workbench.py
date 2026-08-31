from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.schemas import CurationCaseUpdate, CurationProfileUpdate
from packages.platform.curation_workbench import (
    business_label,
    compact_value,
    curation_impacts,
    summarize_fields,
)


def test_business_labels_hide_internal_curation_codes() -> None:
    assert business_label("target", "document_profile") == "文档画像"
    assert business_label("field", "classification") == "主题分类"
    assert business_label("scope", "document_future") == "当前及以后版本"
    assert business_label("batch_status", "publish_failed") == "发布失败"
    assert summarize_fields(["classification", "tags", "keywords"]) == "主题分类、标签、关键词"


def test_business_values_are_compact_and_readable() -> None:
    assert compact_value(["产品", "手册"]) == "产品、手册"
    assert compact_value({"start": "2026-01-01", "end": "2026-12-31"}) == "2026-01-01 至 2026-12-31"
    assert compact_value("很长的内容文本", limit=5) == "很长的内…"


def test_impacts_explain_downstream_projection_work() -> None:
    assert "重新切片" in curation_impacts("content_element", "text")
    assert "向量检索" in curation_impacts("chunk", "boost")
    assert "知识图谱" in curation_impacts("entity", "canonical_name")


def test_ignored_case_requires_a_reason() -> None:
    with pytest.raises(ValidationError):
        CurationCaseUpdate(status="ignored", resolution="ignored", reason_note="  ")
    payload = CurationCaseUpdate(status="ignored", resolution="ignored", reason_note="误报")
    assert payload.reason_note == "误报"


def test_profile_batch_rejects_unknown_or_empty_changes() -> None:
    with pytest.raises(ValidationError):
        CurationProfileUpdate(space_id="space", changes={}, reason_note="修正")
    with pytest.raises(ValidationError):
        CurationProfileUpdate(space_id="space", changes={"quality_score": 100}, reason_note="修正")
    payload = CurationProfileUpdate(
        space_id="space",
        changes={"classification": "产品资料", "tags": ["产品", "手册"]},
        reason_note="业务专家修正",
    )
    assert payload.scope == "version_only"
