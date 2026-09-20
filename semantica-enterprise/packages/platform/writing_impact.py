"""Preview safe, block-scoped changes in Plate content.

New content updates stable numeric occurrences, not matching strings. Legacy
block-only bindings retain the unique-number compatibility path; qualitative,
ambiguous or stale content is left for human review.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .writing_occurrences import (
    iter_numeric_occurrences,
    numeric_value,
    propose_numeric_occurrence_changes,
)


def _display_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("number", value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            number = numeric_value(value)
        except ValueError:
            return None
        return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
    return None


def _text_leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    if isinstance(node.get("text"), str):
        leaves.append(node)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            leaves.extend(_text_leaves(child))
    return leaves


def _number_pattern(value: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\d.])" + re.escape(value) + r"(?![\d.])")


def propose_bound_text_change(
    node: dict[str, Any],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a reviewable Plate-node proposal without mutating its source."""
    original = deepcopy(node)
    proposed = deepcopy(node)
    if any(iter_numeric_occurrences(node)):
        try:
            return propose_numeric_occurrence_changes(node, changes)
        except ValueError as exc:
            return {
                "selectable": False, "reason": str(exc),
                "old_text": "".join(leaf["text"] for leaf in _text_leaves(original)),
            }
    if str(node.get("type") or "") not in {"p", "table", "li"}:
        return {"selectable": False, "reason": "此内容需要人工核对", "old_text": "".join(leaf["text"] for leaf in _text_leaves(original))}
    original_leaves = _text_leaves(original)
    leaves = _text_leaves(proposed)
    old_text = "".join(leaf["text"] for leaf in original_leaves)
    if not changes:
        return {"selectable": False, "reason": "没有可验证的数值变化", "old_text": old_text}
    edits: dict[int, list[tuple[int, int, str]]] = {}
    for change in changes:
        old_value = _display_value(change.get("old_value"))
        new_value = _display_value(change.get("new_value"))
        if old_value is None or new_value is None:
            return {"selectable": False, "reason": "非数值事实需要人工改写", "old_text": old_text}
        pattern = _number_pattern(old_value)
        # Match only the immutable baseline. One change must not accidentally
        # consume another change's replacement value.
        occurrences = [(index, match) for index, leaf in enumerate(original_leaves) for match in pattern.finditer(leaf["text"])]
        if len(occurrences) > 1:
            return {"selectable": False, "reason": "旧数值未唯一出现，不能安全替换", "old_text": old_text}
        if not occurrences:
            continue
        index, match = occurrences[0]
        leaf_edits = edits.setdefault(index, [])
        if any(match.start() < end and match.end() > start for start, end, _value in leaf_edits):
            return {"selectable": False, "reason": "同一数值对应多个变更，需补充精确位置绑定", "old_text": old_text}
        leaf_edits.append((match.start(), match.end(), new_value))
    if not edits:
        # The block has a registered dependency but does not repeat the changed
        # numeric value.  A reviewer may explicitly keep its wording while the
        # binding is advanced to the new immutable Fact/Computation versions.
        # This is intentionally different from an automatic edit.
        proposed["freshness_status"] = "current"
        return {
            "selectable": True,
            "review_only": True,
            "reason": "本段依赖已变化，但正文未出现待替换数值；可在复核后保留原文",
            "old_text": old_text,
            "new_text": old_text,
            "new_node": proposed,
        }
    for index, replacements in edits.items():
        for start, end, value in sorted(replacements, reverse=True):
            leaves[index]["text"] = leaves[index]["text"][:start] + value + leaves[index]["text"][end:]
    return {
        "selectable": True,
        "binding_mode": "legacy_unique_number",
        "reason": "仅替换已绑定数值，引用与其他文字保持原样",
        "old_text": old_text,
        "new_text": "".join(leaf["text"] for leaf in leaves),
        "new_node": proposed,
    }


def find_plate_node(content: list[dict[str, Any]], block_id: str) -> dict[str, Any] | None:
    def walk(node: dict[str, Any]) -> dict[str, Any] | None:
        if str(node.get("id") or "") == block_id:
            return node
        for child in node.get("children") or []:
            if isinstance(child, dict):
                found = walk(child)
                if found is not None:
                    return found
        return None

    for node in content:
        found = walk(node)
        if found is not None:
            return found
    return None
