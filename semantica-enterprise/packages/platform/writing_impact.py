"""Preview safe, block-scoped changes in Plate content.

Only an explicitly bound block can receive a proposed text replacement.  A
numeric value must occur exactly once in its text leaves; qualitative claims,
ambiguous values and unbound prose are left for human review.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


def _display_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("number", value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
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
    if str(node.get("type") or "") not in {"p", "table", "li"}:
        return {"selectable": False, "reason": "此内容需要人工核对", "old_text": "".join(leaf["text"] for leaf in _text_leaves(original))}
    original_leaves = _text_leaves(original)
    leaves = _text_leaves(proposed)
    old_text = "".join(leaf["text"] for leaf in original_leaves)
    if not changes:
        return {"selectable": False, "reason": "没有可验证的数值变化", "old_text": old_text}
    replacements = 0
    for change in changes:
        old_value = _display_value(change.get("old_value"))
        new_value = _display_value(change.get("new_value"))
        if old_value is None or new_value is None:
            return {"selectable": False, "reason": "非数值事实需要人工改写", "old_text": old_text}
        pattern = _number_pattern(old_value)
        occurrences = [(leaf, match) for leaf in leaves for match in pattern.finditer(leaf["text"])]
        if len(occurrences) > 1:
            return {"selectable": False, "reason": "旧数值未唯一出现，不能安全替换", "old_text": old_text}
        if not occurrences:
            continue
        leaf, match = occurrences[0]
        leaf["text"] = leaf["text"][:match.start()] + new_value + leaf["text"][match.end():]
        replacements += 1
    if replacements == 0:
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
    return {
        "selectable": True,
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
