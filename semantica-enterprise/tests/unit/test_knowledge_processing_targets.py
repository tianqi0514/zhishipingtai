from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.schemas import KnowledgeProcessingRequest
from packages.platform.knowledge_processing import (
    completed_processing_targets,
    normalize_processing_mode,
    processing_mode_for_targets,
    processing_targets,
    version_in_graph_projection,
    version_in_vector_projection,
)


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")
ROUTES = (ROOT / "apps/api/routes.py").read_text(encoding="utf-8")
WORKER = (ROOT / "apps/worker/tasks.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mode", "targets"),
    [
        ("vector", {"vector"}),
        ("graph", {"graph"}),
        ("both", {"vector", "graph"}),
    ],
)
def test_processing_mode_maps_to_explicit_targets(mode: str, targets: set[str]) -> None:
    assert normalize_processing_mode(mode) == mode
    assert set(processing_targets(mode)) == targets


def test_processing_mode_is_strict_and_defaults_to_both() -> None:
    assert normalize_processing_mode(None) == "both"
    assert KnowledgeProcessingRequest().mode == "both"
    with pytest.raises(ValueError):
        normalize_processing_mode("all")
    with pytest.raises(ValidationError):
        KnowledgeProcessingRequest(mode="all")


def test_effective_mode_is_derived_from_completed_targets() -> None:
    assert processing_mode_for_targets({"vector"}) == "vector"
    assert processing_mode_for_targets({"graph"}) == "graph"
    assert processing_mode_for_targets({"vector", "graph"}) == "both"


def test_completed_targets_are_additive_and_legacy_safe() -> None:
    assert completed_processing_targets(None) == set()
    assert completed_processing_targets({"knowledge_targets_completed": ["graph"]}) == {"graph"}
    assert completed_processing_targets({"knowledge_targets_completed": ["vector", "graph"]}) == {
        "vector",
        "graph",
    }
    assert completed_processing_targets({"knowledge_status": "published"}) == {"vector", "graph"}


def test_graph_only_versions_are_excluded_from_search_projection() -> None:
    assert version_in_vector_projection(None) is True
    assert version_in_vector_projection({"knowledge_processing_mode": "graph"}) is False
    assert version_in_vector_projection({"knowledge_processing_mode": "vector"}) is False
    assert version_in_vector_projection({"knowledge_processing_mode": "both"}) is False
    assert version_in_vector_projection({"knowledge_processing_protocol": "targets_v1"}) is False
    assert version_in_vector_projection({"knowledge_status": "published"}) is True
    assert version_in_vector_projection({"knowledge_targets_completed": ["graph"]}) is False
    assert version_in_vector_projection({"knowledge_targets_completed": ["vector"]}) is True
    assert version_in_vector_projection({"knowledge_targets_completed": ["graph", "vector"]}) is True


def test_pending_and_vector_only_versions_are_excluded_from_graph_projection() -> None:
    assert version_in_graph_projection(None) is True
    assert version_in_graph_projection({"knowledge_processing_mode": "graph"}) is False
    assert version_in_graph_projection({"knowledge_processing_protocol": "targets_v1"}) is False
    assert version_in_graph_projection({"knowledge_status": "published"}) is True
    assert version_in_graph_projection({"knowledge_targets_completed": ["vector"]}) is False
    assert version_in_graph_projection({"knowledge_targets_completed": ["graph"]}) is True


def test_upload_and_manual_processing_send_real_mode_to_backend() -> None:
    assert "knowledgeProcessingField" in APP
    assert "knowledge_processing_mode" in APP
    assert "fd.set('knowledge_processing_mode'" in APP
    assert "body:{mode:d.knowledge_processing_mode}" in APP
    assert 'knowledge_processing_mode: str = Form("both")' in ROUTES
    assert "payload: KnowledgeProcessingRequest | None" in ROUTES
    assert "knowledge-processing-picker" in STYLE


def test_worker_skips_each_unselected_projection() -> None:
    assert 'execute_semantic_extraction = graph_enabled and generic_semantic_extraction_enabled' in WORKER
    assert '"processing_mode_excludes_graph"' in WORKER
    assert '"processing_mode_excludes_vector"' in WORKER
    assert "if vector_enabled:" in WORKER
    assert "if graph_enabled:" in WORKER
    assert "include_pending_version_ids={version.id}" in WORKER
    assert "include_pending_entity_ids=pending_entity_ids" in WORKER
    assert "requested_targets.update(completed_targets)" in WORKER
    assert "successful_targets.discard(\"graph\")" in WORKER


def test_ui_explains_graph_extraction_and_mode_on_jobs() -> None:
    assert "图谱语义抽取" in APP
    assert "本次选择“仅检索”，已跳过图谱加工" in APP
    assert "本次选择“仅图谱”，已跳过检索索引" in APP
    assert "加工方式" in APP
