from __future__ import annotations

from pathlib import Path


APP_JS = Path(__file__).parents[2] / "apps" / "api" / "static" / "app.js"


def _source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_frontend_understands_every_effective_space_permission_level() -> None:
    source = _source()
    assert "SPACE_PERMISSION_LEVEL={read:1,write:2,manage:3,owner:4,admin:5}" in source
    assert "function currentSpaceCan(required='read')" in source
    assert "effective_permission" in source
    assert "只读" in source
    assert "空间负责人" in source


def test_read_only_space_hides_primary_mutation_entry_points() -> None:
    source = _source()
    for selector in (
        '#doc-upload',
        '[data-action="doc-edit"]',
        '#source-add',
        '[data-action="source-delete"]',
        '#graph-add-node',
        '#graph-edge-delete',
        '[data-action="case-correct"]',
        '[data-db-action="edit-mapping"]',
        '[data-media-action="reprocess"]',
        '[data-action="job-retry"]',
        '[data-action="run-publish"]',
    ):
        assert f"'{selector}'" in source
    assert "control.hidden=true;control.disabled=true" in source
    assert "spacePermissionObserver.observe" in source


def test_manage_only_controls_are_separate_from_write_controls() -> None:
    source = _source()
    for selector in (
        '[data-db-action="delete-mapping"]',
        '#analysis-task-create',
        '[data-action="task-edit"]',
        '#analysis-set-add',
        '[data-action="rule-delete"]',
    ):
        assert f"'{selector}'" in source
    assert "apply(SPACE_WRITE_CONTROL_SELECTORS,'write')" in source
    assert "apply(SPACE_MANAGE_CONTROL_SELECTORS,'manage')" in source


def test_space_table_only_renders_management_actions_for_manage_permission() -> None:
    source = _source()
    assert "const manageable=spaceCan(x,'manage')" in source
    assert "manageable?`${btn('权限','space-grants'" in source
    assert "spacePermissionLabel(x)" in source
