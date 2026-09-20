"""Position-level bindings for authoritative values in immutable Plate versions.

Offsets are used only once, when binding validated Agent output.  Thereafter an
isolated text leaf (or one-leaf inline node) owns a stable occurrence identifier.
Updates address that identifier and authority, never search the document for a
number.  This module neither verifies a Fact nor commits a document version.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any, Iterator, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


BINDING_KEY = "writing_binding"
_UNSET = object()


class NumericOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_id: StrictStr = Field(min_length=1, max_length=200)
    fact_key: StrictStr = Field(min_length=1, max_length=300)
    fact_id: StrictStr | None = None
    fact_version: StrictInt | None = Field(default=None, ge=1)
    computation_run_id: StrictStr | None = None
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    evidence_type: Literal["writing_evidence", "source_chunk"] = "writing_evidence"
    value: Any
    unit: StrictStr = Field(default="", max_length=40)
    display_unit: StrictStr | None = Field(default=None, max_length=40)
    scale: StrictStr | StrictInt | StrictFloat | None = None
    decimal_places: StrictInt | None = Field(default=None, ge=0, le=12)
    grouping: StrictBool = False
    show_unit: StrictBool = False
    rounding: StrictStr = "half_up"


def numeric_value(value: Any) -> Decimal:
    if isinstance(value, dict):
        value = value.get("number") if "number" in value else value.get("value")
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("受控数值缺失或不是有效数值")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("受控数值无效") from exc
    if not number.is_finite() or abs(number.adjusted()) > 100:
        raise ValueError("受控数值必须是范围内的有限数值")
    return number


_UNITS = {
    "元": ("currency", Decimal(1)),
    "万元": ("currency", Decimal(10000)),
    "亿元": ("currency", Decimal(100000000)),
    "人": ("person", Decimal(1)),
    "万人": ("person", Decimal(10000)),
    "张": ("bed", Decimal(1)),
    "顶": ("tent", Decimal(1)),
    "平方米": ("area", Decimal(1)),
    "㎡": ("area", Decimal(1)),
    "m²": ("area", Decimal(1)),
    "万平方米": ("area", Decimal(10000)),
    "比例": ("ratio", Decimal(1)),
    "ratio": ("ratio", Decimal(1)),
    "%": ("ratio", Decimal("0.01")),
    "百分比": ("ratio", Decimal("0.01")),
}


def _scale(binding: NumericOccurrence) -> Decimal:
    target = binding.display_unit if binding.display_unit is not None else binding.unit
    if target == binding.unit:
        scale = Decimal(1)
    else:
        source_unit, target_unit = _UNITS.get(binding.unit), _UNITS.get(target)
        if source_unit is None or target_unit is None or source_unit[0] != target_unit[0]:
            raise ValueError("显示单位与原始单位不相容")
        scale = source_unit[1] / target_unit[1]
    if binding.scale is not None:
        if isinstance(binding.scale, bool):
            raise ValueError("显示换算比例无效")
        try:
            explicit = Decimal(str(binding.scale))
        except InvalidOperation as exc:
            raise ValueError("显示换算比例无效") from exc
        if not explicit.is_finite() or explicit != scale:
            raise ValueError("显示换算比例与单位换算不一致")
    return scale


def render_numeric_occurrence(binding: NumericOccurrence | dict[str, Any], value: Any = _UNSET) -> str:
    entry = binding if isinstance(binding, NumericOccurrence) else NumericOccurrence.model_validate(binding)
    if not entry.fact_id and not entry.computation_run_id:
        raise ValueError("位置绑定缺少权威事实或计算记录")
    if entry.fact_id and entry.fact_version is None:
        raise ValueError("位置绑定缺少事实版本")
    if entry.rounding != "half_up":
        raise ValueError("不支持的显示舍入规则")
    with localcontext() as ctx:
        ctx.prec = 128
        result = numeric_value(entry.value if value is _UNSET else value) * _scale(entry)
        if entry.decimal_places is not None:
            result = result.quantize(Decimal(1).scaleb(-entry.decimal_places), rounding=ROUND_HALF_UP)
            text = format(result, f",.{entry.decimal_places}f" if entry.grouping else f".{entry.decimal_places}f")
        else:
            text = format(result, ",f" if entry.grouping else "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
        if text in {"-0", "-0.0"}:
            text = text[1:]
    return text + ((entry.display_unit if entry.display_unit is not None else entry.unit) if entry.show_unit else "")


def _text(node: dict[str, Any]) -> str:
    return str(node.get("text") or "") + "".join(
        _text(child) for child in node.get("children") or [] if isinstance(child, dict)
    )


def iter_numeric_occurrences(node: dict[str, Any], path: tuple[int, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[int, ...]]]:
    if BINDING_KEY in node:
        yield node, path
    for index, child in enumerate(node.get("children") or []):
        if isinstance(child, dict):
            yield from iter_numeric_occurrences(child, (*path, index))


def validate_numeric_occurrences(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate local shape/display only; the API must authorize source IDs."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item, path in iter_numeric_occurrences(node):
        metadata = NumericOccurrence.model_validate(item[BINDING_KEY])
        if metadata.occurrence_id in seen:
            raise ValueError("位置绑定标识重复，请重新确认粘贴内容")
        seen.add(metadata.occurrence_id)
        if "text" not in item:
            children = item.get("children") or []
            if len(children) != 1 or not isinstance(children[0], dict) or not isinstance(children[0].get("text"), str):
                raise ValueError("受控行内内容必须只有一个数值文本叶节点")
            if BINDING_KEY in children[0]:
                raise ValueError("位置绑定不能嵌套")
        elif not isinstance(item["text"], str):
            raise ValueError("受控内容不是文本")
        elif item.get("children"):
            raise ValueError("文本叶节点不能包含子节点")
        if _text(item) != render_numeric_occurrence(metadata):
            raise ValueError("受控正文与绑定值不一致，请通过指标变更修改")
        result.append({**metadata.model_dump(), "leaf_path": list(path)})
    return result


def bind_numeric_occurrences(node: dict[str, Any], specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Split explicitly supplied text ranges into stable, exactly-bound leaves.

    Each spec contains ``leaf_path``, half-open ``start``/``end`` offsets, and
    ``binding`` (NumericOccurrence fields including the server-resolved value).
    A missing occurrence_id is deterministically generated from block/path/range
    and source identity.  Source text, formatting and citations are preserved.
    """
    proposed = deepcopy(node)
    if specs and (not isinstance(node.get("id"), str) or not node["id"].strip()):
        raise ValueError("数值位置所在正文块缺少稳定标识")
    by_path: dict[tuple[int, ...], list[dict[str, Any]]] = {}
    for spec in specs:
        if set(spec) != {"leaf_path", "start", "end", "binding"}:
            raise ValueError("位置绑定字段不完整或包含未支持字段")
        path = spec["leaf_path"]
        if not isinstance(path, list) or not path or any(type(i) is not int or i < 0 for i in path):
            raise ValueError("位置绑定路径无效")
        if type(spec["start"]) is not int or type(spec["end"]) is not int:
            raise ValueError("位置绑定范围必须是整数")
        by_path.setdefault(tuple(path), []).append(spec)
    # Sibling leaves may expand, so process paths from the last leaf backwards.
    for path, grouped in sorted(by_path.items(), reverse=True):
        parent = proposed
        try:
            for index in path[:-1]:
                if BINDING_KEY in parent:
                    raise ValueError("不能在已有受控内容内嵌套绑定")
                parent = parent["children"][index]
            leaf = parent["children"][path[-1]]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("位置绑定路径不存在") from exc
        if not isinstance(leaf.get("text"), str) or BINDING_KEY in leaf or BINDING_KEY in parent:
            raise ValueError("只能为未绑定的文本叶节点登记位置")
        text = leaf["text"]
        cursor = 0
        pieces: list[dict[str, Any]] = []
        for spec in sorted(grouped, key=lambda value: value["start"]):
            start, end = spec["start"], spec["end"]
            if type(start) is not int or type(end) is not int or start < cursor or end <= start or end > len(text):
                raise ValueError("位置绑定范围重复或越界")
            binding = deepcopy(spec["binding"])
            if not isinstance(binding, dict):
                raise ValueError("位置绑定不是结构化对象")
            if not binding.get("occurrence_id"):
                identity = f"{node.get('id')}:{path}:{start}:{end}:{binding.get('fact_key')}:{binding.get('fact_id')}:{binding.get('computation_run_id')}"
                binding["occurrence_id"] = str(uuid5(NAMESPACE_URL, identity))
            metadata = NumericOccurrence.model_validate(binding)
            if text[start:end] != render_numeric_occurrence(metadata):
                raise ValueError("指定位置文本不等于权威数值的显示结果")
            if start > cursor:
                pieces.append({**leaf, "text": text[cursor:start]})
            pieces.append({**leaf, "text": text[start:end], BINDING_KEY: metadata.model_dump()})
            cursor = end
        if cursor < len(text):
            pieces.append({**leaf, "text": text[cursor:]})
        parent["children"][path[-1]:path[-1] + 1] = pieces
    validate_numeric_occurrences(proposed)
    return proposed


def propose_numeric_occurrence_changes(node: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Preview only exact authorities; repeated/unbound equal values are safe."""
    proposed = deepcopy(node)
    validate_numeric_occurrences(proposed)
    changes_by_key: dict[str, dict[str, Any]] = {}
    for change in changes:
        key = str(change.get("fact_key") or change.get("result_key") or "")
        if not key:
            raise ValueError("位置级更新必须提供事实或指标标识")
        if key in changes_by_key:
            raise ValueError("同一事实有重复变更")
        numeric_value(change.get("old_value"))
        numeric_value(change.get("new_value"))
        changes_by_key[key] = change
    updates: list[dict[str, Any]] = []
    for item, path in iter_numeric_occurrences(proposed):
        binding = NumericOccurrence.model_validate(item[BINDING_KEY])
        change = changes_by_key.get(binding.fact_key)
        if change is None:
            continue
        if binding.fact_id and change.get("fact_id") and binding.fact_id != change["fact_id"]:
            raise ValueError("正文引用历史事实版本，请先核对待更新内容")
        old_version = change.get("fact_version", change.get("version"))
        if binding.fact_version is not None and old_version is not None and binding.fact_version != old_version:
            raise ValueError("事实版本已变化，请重新预览")
        old_run = change.get("previous_run_id", change.get("computation_run_id"))
        if binding.computation_run_id and old_run and binding.computation_run_id != old_run:
            raise ValueError("计算版本已变化，请重新预览")
        if change.get("unit") is not None and binding.unit != change["unit"]:
            raise ValueError("事实单位已变化，请重新核对显示口径")
        if numeric_value(binding.value) != numeric_value(change["old_value"]):
            raise ValueError("绑定值与变更基线不一致，请重新预览")
        old_text = _text(item)
        new_text = render_numeric_occurrence(binding, change["new_value"])
        updated = binding.model_dump()
        updated["value"] = deepcopy(change["new_value"])
        item[BINDING_KEY] = updated
        if "text" in item:
            item["text"] = new_text
        else:
            item["children"][0]["text"] = new_text
        updates.append({
            "occurrence_id": binding.occurrence_id, "fact_key": binding.fact_key,
            "leaf_path": list(path), "old_text": old_text, "new_text": new_text,
            "fact_id": binding.fact_id, "fact_version": binding.fact_version,
            "computation_run_id": binding.computation_run_id,
            "unit": binding.unit, "display_unit": binding.display_unit or binding.unit,
            "evidence_ids": list(binding.evidence_ids),
        })
    if not updates:
        return {"selectable": False, "reason": "变更未命中登记的数值位置，需人工核对", "old_text": _text(node)}
    return {
        "selectable": True, "binding_mode": "occurrence", "occurrence_updates": updates,
        "reason": "按已登记指标位置更新；相同但未绑定的数字保持不变",
        "old_text": _text(node), "new_text": _text(proposed), "new_node": proposed,
    }


def rebind_numeric_occurrences(
    node: dict[str, Any], *, fact_replacements: dict[str, dict[str, Any]], run_replacements: dict[str, str],
) -> dict[str, Any]:
    """Advance selected projection IDs after transactional authority creation.

    fact_replacements maps old ID to {id, version}; caller resolves these from
    newly persisted server records, never Agent input. Evidence is retained.
    """
    proposed = deepcopy(node)
    for item, _path in iter_numeric_occurrences(proposed):
        binding = NumericOccurrence.model_validate(item[BINDING_KEY]).model_dump()
        replacement = fact_replacements.get(binding.get("fact_id") or "")
        if replacement:
            binding["fact_id"] = replacement["id"]
            binding["fact_version"] = replacement["version"]
        run_id = binding.get("computation_run_id")
        if run_id in run_replacements:
            binding["computation_run_id"] = run_replacements[run_id]
        item[BINDING_KEY] = NumericOccurrence.model_validate(binding).model_dump()
    validate_numeric_occurrences(proposed)
    return proposed


def validate_numeric_occurrence_edit(
    previous_content: list[dict[str, Any]], next_content: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Guard ordinary editor saves against changing self-asserted authority.

    A metadata/display-consistent forged value is still not authoritative.
    Normal editing may move or format a bound leaf, but cannot create or change
    its binding. Deletions are reported for the caller's review/stale policy;
    deleting prose never deletes its Fact. New bindings require the dedicated
    server-validated chapter/binding operation.
    """
    previous = {item["occurrence_id"]: item for item in validate_numeric_occurrences({"children": previous_content})}
    current = {item["occurrence_id"]: item for item in validate_numeric_occurrences({"children": next_content})}
    if set(current) - set(previous):
        raise ValueError("普通正文保存不能新增权威位置绑定，请先核对来源")
    moved: list[str] = []
    for occurrence_id, item in current.items():
        before = previous[occurrence_id]
        if {key: value for key, value in before.items() if key != "leaf_path"} != {
            key: value for key, value in item.items() if key != "leaf_path"
        }:
            raise ValueError("受控数值或依据已修改，请使用指标变更预览")
        if before["leaf_path"] != item["leaf_path"]:
            moved.append(occurrence_id)
    return {"removed_occurrence_ids": sorted(set(previous) - set(current)), "moved_occurrence_ids": sorted(moved)}
