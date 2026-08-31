from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.schemas import KnowledgeFactCreate
from packages.platform.models import Fact


ROOT = Path(__file__).resolve().parents[2]


def test_manual_fact_provenance_is_nullable() -> None:
    assert Fact.__table__.c.source_chunk_id.nullable is True


def test_fact_requires_exactly_one_object_kind() -> None:
    valid = KnowledgeFactCreate(
        space_id="space-1",
        subject_entity_id="subject-1",
        predicate="负责",
        object_entity_id="object-1",
    )
    assert valid.object_entity_id == "object-1"

    with pytest.raises(ValidationError):
        KnowledgeFactCreate(
            space_id="space-1",
            subject_entity_id="subject-1",
            predicate="负责",
        )
    with pytest.raises(ValidationError):
        KnowledgeFactCreate(
            space_id="space-1",
            subject_entity_id="subject-1",
            predicate="负责",
            object_entity_id="object-1",
            object_value="重复客体",
        )


def test_graph_workspace_uses_local_3d_renderer_and_real_crud() -> None:
    html = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    graph_renderer = (ROOT / "apps/api/static/graph3d.js").read_text(encoding="utf-8")

    assert "/assets/graph3d.js" in html
    assert "new window.KnowledgeGraph3D" in javascript
    assert "知识图谱，可拖拽旋转、滚轮缩放、点击节点或关系编辑" in javascript
    assert "'/knowledge/entities'" in javascript
    assert "'/knowledge/facts'" in javascript
    assert "class KnowledgeGraph3D" in graph_renderer
