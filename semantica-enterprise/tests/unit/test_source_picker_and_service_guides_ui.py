from __future__ import annotations

from pathlib import Path
from typing import get_args

from apps.api.schemas import SOURCE_TYPES


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")
STATIC = ROOT / "apps/api/static"


def test_source_picker_lists_every_backend_source_type() -> None:
    category_block = APP.split("const SOURCE_CATEGORIES=", 1)[1].split("const CORE_SOURCE_TYPES", 1)[0]
    for source_type in get_args(SOURCE_TYPES):
        assert f"'{source_type}'" in category_block
    assert "选择数据源类型" in APP
    assert "data-source-type-card" in APP
    assert "配置 · ${sourceTypeName(record.source_type)}" in APP


def test_source_picker_uses_raster_asset() -> None:
    atlas = STATIC / "source-icons/data-source-atlas.png"
    assert atlas.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "data-source-atlas.png" in STYLE
    assert "source-icons/data-source-atlas.svg" not in STYLE


def test_every_open_service_has_a_preview_manual() -> None:
    manuals = {
        "rest": "rest-api.md",
        "mcp": "mcp.md",
        "cli": "cli.md",
        "agent": "agent.md",
    }
    for service_type, filename in manuals.items():
        assert f'data-service-guide="{service_type}"' in APP
        text = (STATIC / "manuals" / filename).read_text(encoding="utf-8")
        assert "接入手册" in text.splitlines()[0]
        assert len(text) > 300


def test_dashboard_grid_panels_do_not_inherit_sibling_margin() -> None:
    assert ".dashboard-grid>.panel+.panel" in STYLE
    assert ".dashboard-panel{display:flex" in STYLE
