"""Register unambiguous authority positions in structured report output.

This is a one-time, server-validated binding step, not an update-by-string
algorithm. Tables require an exact row label, value and column unit. Prose
requires a unique authority for the literal value/unit. Formula constants and
ambiguous values are never registered. Later updates use stable occurrences.
"""
from __future__ import annotations

from copy import deepcopy
import re
from decimal import Decimal
from typing import Any

from .writing_occurrences import bind_numeric_occurrences, numeric_value


NUMBER = re.compile(r"(?<![\d.])[+-]?\d+(?:\.\d+)?(?![\d.])")


def _text(node):
    return str(node.get("text") or "") + "".join(_text(c) for c in node.get("children") or [])


def bind_report_numeric_positions(node: dict[str, Any], metadata: dict[str, Any], *,
    inputs_by_key: dict[str, dict[str, Any]], metrics_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    authorities = []
    for key in metadata.get("input_keys") or []:
        row = inputs_by_key[key]
        if row.get("verification_status") != "verified":
            continue
        try:
            value = numeric_value(row.get("value"))
        except ValueError:
            continue
        authorities.append((str(row.get("label") or ""), value, {
            "fact_key": key, "fact_id": row["id"], "fact_version": row["version"],
            "value": row["value"], "unit": str(row.get("unit") or ""),
        }))
    for key in metadata.get("metric_keys") or []:
        row = metrics_by_key[key]
        output = (row.get("result") or {}).get("output_fact") or {}
        value = (row.get("result") or {}).get("value")
        try:
            number = numeric_value(value)
        except ValueError:
            continue
        authorities.append((str(output.get("label") or ""), number, {
            "fact_key": key, "computation_run_id": row["id"], "value": value,
            "unit": str(output.get("unit") or ""),
        }))
    specs = []

    def register(path, match, binding):
        literal = match.group()
        specs.append({"leaf_path": list(path), "start": match.start(), "end": match.end(),
            "binding": {**binding, "decimal_places": len(literal.split(".")[1]) if "." in literal else 0}})

    def register_unit_literals(path, text):
        for match in NUMBER.finditer(text):
            prefix = text[:match.start()].rstrip()
            operators = {"÷", "×", "/", "*", "=", "+", "−"}
            in_formula = prefix[-1:] in operators or text[match.end():].lstrip()[:1] in operators
            candidates = [binding for name, value, binding in authorities
                if value == Decimal(match.group()) and binding["unit"]
                and text.startswith(binding["unit"], match.end())
                # An explicit variable label and unit distinguish a formula
                # input from its conversion constants, even after × or +.
                and (not in_formula or (name and prefix.endswith(name)))]
            if len(candidates) == 1:
                register(path, match, candidates[0])

    if node.get("type") == "table":
        rows = node.get("children") or []
        headers = [_text(cell) for cell in (rows[0].get("children") or [])] if rows else []
        for ri, row in enumerate(rows[1:], 1):
            cells = row.get("children") or []
            if not cells:
                continue
            label = _text(cells[0]).strip()
            for ci, cell in enumerate(cells[1:], 1):
                leaves = cell.get("children") or []
                if len(leaves) != 1 or not isinstance(leaves[0].get("text"), str):
                    continue
                literal = leaves[0]["text"]
                match = NUMBER.fullmatch(literal)
                if match is None:
                    register_unit_literals((ri, ci, 0), literal)
                    continue
                candidates = [binding for name, value, binding in authorities
                    if name == label and value == Decimal(literal) and binding["unit"]
                    and ci < len(headers) and (headers[ci].strip() == binding["unit"]
                        or binding["unit"] in re.findall(r"[（(]([^（）()]+)[）)]", headers[ci]))]
                if len(candidates) == 1:
                    register((ri, ci, 0), match, candidates[0])
    else:
        # Only direct prose leaves; inline citations and their hidden children
        # must not be interpreted as mutable article numbers.
        for index, leaf in enumerate(node.get("children") or []):
            if not isinstance(leaf.get("text"), str):
                continue
            register_unit_literals((index,), leaf["text"])
    result = bind_numeric_occurrences(node, specs) if specs else deepcopy(node)
    # Registration has already resolved all unambiguous authority positions.
    # Never fall back to searching unbound numbers in this new report block.
    result["numeric_binding_version"] = 1
    return result
