from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable

from packages.platform.writing import TRUSTED_BLOCK_TYPES, content_hash, walk_plate_nodes


PUBLIC_SECTION_TYPES = {"p", "ul", "ol", "blockquote", "table"}
HEADING_TYPES = {"h1", "h2", "h3", "heading1", "heading2", "heading3"}
WORK_NOTE_PATTERNS = (
    r"\bI have\b",
    r"\bLet me\b",
    r"\bI will\b",
    r"\bThe (?:section|fragment|project context)\b",
    r"\bVerified facts\b",
    r"\bgraph query returned\b",
    r"\bsearch results\b",
    r"Let me present this as the final answer",
    r"writing_[a-z0-9_]+",
    r"knowledge_[a-z0-9_]+",
    r"structured_[a-z0-9_]+",
)
PLATFORM_PROCESS_PHRASES = (
    "图谱查询返回为空",
    "本轮已调用知识图谱工具",
    "尚未写入文稿",
    "正在调用工具",
    "检索工具返回",
    "Citations used",
    "Let me count characters",
)

RESULT_SECTION_HINTS = {
    "disaster_grade": ("grading", "assessment", "situation"),
    "rescue_gap": ("actions", "safeguards", "response"),
    "county_bed_gap": ("actions", "safeguards", "response"),
    "all_area_bed_gap": ("actions", "safeguards", "response"),
    "tents_gap": ("safeguards", "actions", "response"),
    "selected_plan": ("safeguards", "actions", "response"),
}


def plate_plain_text(nodes: Iterable[dict[str, Any]]) -> str:
    return "\n".join(
        str(node.get("text") or "")
        for node in walk_plate_nodes(nodes)
        if "text" in node and str(node.get("text") or "").strip()
    )


def _node_text(node: dict[str, Any]) -> str:
    if "text" in node:
        return str(node.get("text") or "")
    return "".join(_node_text(item) for item in node.get("children") or [] if isinstance(item, dict))


def _extract_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as direct_error:
        # Some OpenAI-compatible models emit a visible work preface before the
        # requested structured payload.  Keep that raw text in the append-only
        # Agent session for audit, but never put it in the report.  We only
        # accept one final JSON object that reaches the end of the response;
        # arbitrary trailing prose and partial objects remain fail-closed.
        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        for match in re.finditer(r"\{", text):
            try:
                candidate, end = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if text[end:].strip() or not isinstance(candidate, dict):
                continue
            if isinstance(candidate.get("sections"), list):
                candidates.append(candidate)
        if len(candidates) != 1:
            raise ValueError(
                f"Agent 未返回唯一且完整的分章节 JSON（原始输出第 {direct_error.lineno} 行无法直接解析），已阻止写入正文"
            ) from direct_error
        parsed = candidates[0]
    if not isinstance(parsed, dict):
        raise ValueError("Agent 分章节结果必须是对象")
    return parsed


def validate_and_parse_agent_report(
    value: str,
    section_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept only the formal, structured report protocol from DSH.

    This is deliberately fail-closed: execution notes, unknown sections and
    free-form assistant replies are never repaired into a formal report.
    """
    parsed = _extract_json_object(value)
    if set(parsed) - {"sections", "warnings"}:
        raise ValueError("Agent 分章节结果包含未声明字段")
    sections = parsed.get("sections")
    if not isinstance(sections, list):
        raise ValueError("Agent 分章节结果缺少 sections 数组")
    expected = {str(item["key"]): item for item in section_plan}
    actual_keys = [str(item.get("section_key") or "") for item in sections if isinstance(item, dict)]
    duplicates = sorted(key for key, count in Counter(actual_keys).items() if key and count > 1)
    if duplicates:
        raise ValueError(f"Agent 重复生成章节：{', '.join(duplicates)}")
    missing = [key for key in expected if key not in actual_keys]
    unknown = [key for key in actual_keys if key not in expected]
    if missing or unknown or len(sections) != len(section_plan):
        details = []
        if missing:
            details.append(f"缺少 {', '.join(missing)}")
        if unknown:
            details.append(f"未知 {', '.join(unknown)}")
        raise ValueError("Agent 章节结构不符合场景契约：" + "；".join(details))

    normalized: list[dict[str, Any]] = []
    for raw in sections:
        if not isinstance(raw, dict):
            raise ValueError("Agent 章节必须是对象")
        allowed = {"section_key", "title", "content_nodes", "citation_refs", "metric_refs", "inference_refs", "warnings"}
        if set(raw) - allowed:
            raise ValueError(f"章节 {raw.get('section_key')} 包含未声明字段")
        key = str(raw.get("section_key") or "")
        title = str(raw.get("title") or "").strip()
        expected_title = str(expected[key].get("title") or "").strip()
        if title != expected_title:
            raise ValueError(f"章节 {key} 标题必须为“{expected_title}”")
        content_nodes = raw.get("content_nodes")
        if not isinstance(content_nodes, list) or not content_nodes:
            raise ValueError(f"章节“{title}”没有正文内容")
        clean_nodes: list[dict[str, Any]] = []
        for node in content_nodes:
            if not isinstance(node, dict) or set(node) - {"type", "text", "items"}:
                raise ValueError(f"章节“{title}”正文节点结构不受支持")
            node_type = str(node.get("type") or "p")
            if node_type not in PUBLIC_SECTION_TYPES:
                raise ValueError(f"章节“{title}”包含不允许的正文类型：{node_type}")
            if node_type == "table":
                items = node.get("items")
                if not isinstance(items, list) or not items:
                    raise ValueError(f"章节“{title}”的表格没有数据")
                clean_nodes.append({"type": "table", "items": items})
                continue
            text = str(node.get("text") or "").strip()
            if not text:
                raise ValueError(f"章节“{title}”包含空正文")
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in WORK_NOTE_PATTERNS):
                raise ValueError(f"章节“{title}”包含 Agent 工作过程，已阻止写入正文")
            if any(phrase.casefold() in text.casefold() for phrase in PLATFORM_PROCESS_PHRASES):
                raise ValueError(f"章节“{title}”包含平台执行说明，已阻止写入正文")
            clean_nodes.append({"type": node_type, "text": text})
        normalized.append(
            {
                "section_key": key,
                "title": title,
                "content_nodes": clean_nodes,
                "citation_refs": [int(item) for item in (raw.get("citation_refs") or []) if str(item).isdigit()],
                "warnings": [str(item)[:500] for item in (raw.get("warnings") or [])],
            }
        )
    return normalized


def build_generation_prompt(
    *,
    project_name: str,
    section_plan: list[dict[str, Any]],
    reference_characters: int | None = None,
) -> str:
    sections = [
        {
            "section_key": str(item["key"]),
            "title": str(item["title"]),
            "instruction": str(item.get("instruction") or "根据已核验资料撰写正式业务内容。"),
        }
        for item in section_plan
    ]
    schema_example = {
        "sections": [
            {
                "section_key": sections[0]["section_key"] if sections else "section",
                "title": sections[0]["title"] if sections else "章节",
                "content_nodes": [{"type": "p", "text": "正式正文，只写可交付内容，并使用[1]引用真实来源。"}],
                "citation_refs": [1],
                "metric_refs": [],
                "inference_refs": [],
                "warnings": [],
            }
        ],
        "warnings": [],
    }
    length_contract = ""
    if reference_characters:
        minimum = max(1, int(reference_characters * 0.78))
        target = max(minimum, int(reference_characters))
        maximum = max(target, int(reference_characters * 1.30))
        length_contract = (
            f"九章 content_nodes 中纯正文合计不得少于 {minimum} 个中文字符，"
            f"建议约 {target} 个字符，且不得超过 {maximum} 个字符；"
            "不能靠重复段落、空话或输出工作过程凑字数。"
        )
    return (
        "[妙笔正式报告生成]\n"
        "先调用 writing_get_project_context 取得已核验事实、确定性计算、规则推演和采用方案，"
        "再按需调用 knowledge_search 查找当前知识产品版本中的真实来源。"
        "请为下列报告生成一次且仅一次的全部章节。只输出一个 JSON 对象，不要 Markdown 代码围栏之外的文字。"
        "严禁输出思考过程、自我对话、工具名称、检索说明、英文工作草稿、内部 ID 或系统实现。"
        "权威数值与规则结论仅用于准确叙述，不自行改算；服务端会把其可信节点插入固定章节。"
        "章节内容必须互不重复，写明责任主体、执行动作、完成时限或触发条件；证据不足时在 warnings 声明。"
        f"{length_contract}"
        f"\n报告任务：{project_name}\n章节契约：{json.dumps(sections, ensure_ascii=False)}"
        f"\n输出结构示例：{json.dumps(schema_example, ensure_ascii=False)}"
    )


def validate_agent_edit(action: str, original_text: str, suggested_text: str) -> str:
    """Reject work notes and no-op/contract-breaking local edit output."""
    value = suggested_text.strip()
    if value.startswith("```") or not value:
        raise ValueError("Agent 未返回可直接使用的正文建议")
    if any(re.search(pattern, value, re.IGNORECASE) for pattern in WORK_NOTE_PATTERNS):
        raise ValueError("Agent 修改建议包含工作过程，已阻止写入正文")
    if any(phrase.casefold() in value.casefold() for phrase in PLATFORM_PROCESS_PHRASES):
        raise ValueError("Agent 修改建议包含平台执行说明，已阻止写入正文")
    if value == original_text.strip():
        raise ValueError("Agent 修改建议与原文相同")
    if action == "shorten" and len(value) >= len(original_text.strip()):
        raise ValueError("缩写结果没有缩短原文")
    if action == "expand" and len(value) <= len(original_text.strip()):
        raise ValueError("扩写结果没有补充有效内容")
    return value


def _plate_node(node_type: str, text: str, node_id: str) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "children": [{"text": text}]}


def _browser_json_value(value: Any) -> Any:
    """Normalize numbers to the representation Plate can round-trip."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_browser_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _browser_json_value(item) for key, item in value.items()}
    return value


def _table_node(items: list[Any], node_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row_index, raw_row in enumerate(items):
        values = raw_row if isinstance(raw_row, list) else [raw_row]
        rows.append(
            {
                "id": f"{node_id}-r{row_index + 1}",
                "type": "tr",
                "children": [
                    {
                        "id": f"{node_id}-r{row_index + 1}-c{column_index + 1}",
                        "type": "td",
                        "children": [{"text": str(value)}],
                    }
                    for column_index, value in enumerate(values)
                ],
            }
        )
    return {"id": node_id, "type": "table", "children": rows}


def _select_section(section_keys: list[str], result_key: str) -> str:
    for candidate in RESULT_SECTION_HINTS.get(result_key, ()):
        if candidate in section_keys:
            return candidate
    return section_keys[0]


def assemble_report_content(
    *,
    run_id: str,
    title: str,
    section_plan: list[dict[str, Any]],
    agent_sections: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    computations: list[dict[str, Any]],
    inference_facts: list[dict[str, Any]],
    selected_plan: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assemble normal prose and server-owned authority nodes by section."""
    section_keys = [str(item["key"]) for item in section_plan]
    by_section: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {key: [] for key in section_keys}
    for row in computations:
        result = dict(row.get("result") or {})
        output = dict(result.get("output_fact") or {})
        result_key = str(output.get("fact_key") or "")
        if not result_key:
            continue
        unit = str(output.get("unit") or "")
        label = str(output.get("label") or result.get("operation") or "确定性测算")
        block_id = f"metric-{row['id']}"
        node = {
            "id": block_id,
            "type": "computed_metric",
            "label": label,
            "value": result.get("value"),
            "unit": unit,
            "formula": result.get("operation"),
            "dependencies": result.get("dependencies") or {},
            "computation_run_id": row["id"],
            "evidence_ids": row.get("input_fact_ids") or [],
            "freshness_status": "current",
            "children": [{"text": f"经核验与测算，{label}为{result.get('value')}{unit}。"}],
        }
        binding = {
            "block_id": block_id,
            "block_type": "computed_metric",
            "source_type": "computation",
            "source_id": row["id"],
            "computation_run_id": row["id"],
            "evidence_ids": row.get("input_fact_ids") or [],
            "verification_status": "verified",
            "freshness_status": "current",
            "metadata": {"formula": result.get("operation"), "dependencies": result.get("dependencies") or {}},
        }
        by_section[_select_section(section_keys, result_key)].append((node, binding))
    for fact in inference_facts:
        block_id = f"inference-{fact['id']}"
        locator = dict(fact.get("source_locator") or {})
        value = dict(fact.get("value") or {})
        display_value = value.get("text", value.get("number", value.get("value", "")))
        node = {
            "id": block_id,
            "type": "inference_conclusion",
            "label": fact.get("label") or "规则推演结论",
            "fact_id": fact["id"],
            "source_id": fact.get("source_id"),
            "source_version": fact.get("source_version"),
            "source_locator": locator,
            "evidence_ids": [
                str(item.get("source_fact_id"))
                for item in locator.get("evidence") or []
                if isinstance(item, dict) and item.get("source_fact_id")
            ],
            "freshness_status": "current",
            "children": [{"text": f"经业务规则推演，{fact.get('label') or '当前结论'}为{display_value}。"}],
        }
        binding = {
            "block_id": block_id,
            "block_type": "inference_conclusion",
            "source_type": "semantica_inference",
            "source_id": fact.get("source_id"),
            "source_version": fact.get("source_version"),
            "fact_id": fact["id"],
            "evidence_ids": node["evidence_ids"],
            "verification_status": fact.get("verification_status") or "unverified",
            "freshness_status": "current",
            "metadata": {"rule_id": locator.get("rule_id"), "reasoning_run_id": fact.get("source_id")},
        }
        by_section[_select_section(section_keys, str(fact.get("fact_key") or "disaster_grade"))].append((node, binding))
    if selected_plan:
        route = dict((selected_plan.get("result") or {}).get("route") or {})
        block_id = f"plan-{selected_plan['id']}"
        node = {
            "id": block_id,
            "type": "alternative_plan",
            "label": "已采用处置方案",
            "source_id": selected_plan["id"],
            "plan_key": selected_plan.get("plan_key"),
            "freshness_status": "current",
            "children": [{"text": f"采用{selected_plan.get('name')}：{' → '.join(route.get('path') or [])}，预计{route.get('minutes', '—')}分钟。"}],
        }
        binding = {
            "block_id": block_id,
            "block_type": "alternative_plan",
            "source_type": "mcp_tool",
            "source_id": selected_plan["id"],
            "evidence_ids": [],
            "verification_status": "verified",
            "freshness_status": "current",
            "metadata": {"algorithm": selected_plan.get("algorithm") or {}, "plan_key": selected_plan.get("plan_key")},
        }
        by_section[_select_section(section_keys, "selected_plan")].append((node, binding))

    citations_by_number = {int(item["citation_number"]): item for item in citations}
    content: list[dict[str, Any]] = [_plate_node("h1", title, f"report-{run_id}-title")]
    bindings: list[dict[str, Any]] = []
    sequence = 0
    for section in agent_sections:
        key = str(section["section_key"])
        sequence += 1
        content.append(_plate_node("h2", str(section["title"]), f"report-{run_id}-section-{sequence}"))
        for node_index, raw in enumerate(section["content_nodes"], 1):
            node_id = f"report-{run_id}-{sequence}-{node_index}"
            if raw["type"] == "table":
                content.append(_table_node(raw["items"], node_id))
                continue
            text = str(raw["text"])
            children: list[dict[str, Any]] = []
            cursor = 0
            for match in re.finditer(r"\[(\d{1,3})\]", text):
                number = int(match.group(1))
                citation = citations_by_number.get(number)
                if citation is None:
                    continue
                if match.start() > cursor:
                    children.append({"text": text[cursor:match.start()]})
                snapshot = dict(citation.get("snapshot") or {})
                citation_id = f"citation-{run_id}-{sequence}-{node_index}-{number}-{match.start()}"
                reference = {
                    "id": citation_id,
                    "type": "knowledge_citation",
                    "citation_label": f"[{number}]",
                    "source_title": snapshot.get("title"),
                    "chunk_id": citation.get("chunk_id"),
                    "query_run_id": citation.get("query_run_id"),
                    "source_id": snapshot.get("document_id"),
                    "source_version": snapshot.get("version_number"),
                    "source_locator": {
                        "page_number": snapshot.get("page_number"),
                        "structural_path": snapshot.get("structural_path"),
                    },
                    "freshness_status": "current",
                    "children": [{"text": ""}],
                }
                children.append(reference)
                bindings.append(
                    {
                        "block_id": citation_id,
                        "block_type": "knowledge_citation",
                        "source_type": "policy_document",
                        "source_id": snapshot.get("document_id"),
                        "source_version": snapshot.get("version_number"),
                        "chunk_id": citation.get("chunk_id"),
                        "retrieval_query_run_id": citation.get("query_run_id"),
                        "evidence_ids": [],
                        "content_hash": content_hash(reference),
                        "verification_status": "verified",
                        "freshness_status": "current",
                        "metadata": {
                            "citation_number": number,
                            "rank": citation.get("rank"),
                            "source_title": snapshot.get("title"),
                            "document_version": snapshot.get("version_number"),
                            "page_number": snapshot.get("page_number"),
                            "structural_path": snapshot.get("structural_path"),
                            "content_hash_algorithm": "canonical-json-v1",
                        },
                    }
                )
                cursor = match.end()
            if cursor < len(text):
                children.append({"text": text[cursor:]})
            content.append({"id": node_id, "type": raw["type"], "children": children or [{"text": text}]})
        for trusted_node, binding in by_section.get(key, []):
            trusted_node = _browser_json_value(trusted_node)
            binding["content_hash"] = content_hash(trusted_node)
            binding["metadata"] = {**binding.get("metadata", {}), "content_hash_algorithm": "canonical-json-v1", "section_key": key}
            content.append(trusted_node)
            bindings.append(binding)
    return content, bindings


def report_quality_review(
    content: list[dict[str, Any]],
    *,
    section_plan: list[dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
    expected_computation_count: int = 0,
    expected_inference_count: int = 0,
    reference_characters: int | None = None,
) -> dict[str, Any]:
    text = plate_plain_text(content)
    issues: list[dict[str, Any]] = []
    headings = [_node_text(node).strip() for node in walk_plate_nodes(content) if str(node.get("type") or "") in HEADING_TYPES]
    chapter_occurrences = {
        str(section["title"]): headings.count(str(section["title"]).strip()) for section in section_plan
    }
    for title, count in chapter_occurrences.items():
        if count != 1:
            issues.append({"code": "chapter_occurrence", "severity": "error", "message": f"章节“{title}”应出现 1 次，实际 {count} 次"})
    work_matches = sum(len(re.findall(pattern, text, re.IGNORECASE)) for pattern in WORK_NOTE_PATTERNS)
    process_matches = sum(text.casefold().count(phrase.casefold()) for phrase in PLATFORM_PROCESS_PHRASES)
    if work_matches:
        issues.append({"code": "agent_work_note", "severity": "error", "message": f"正文包含 {work_matches} 处 Agent 工作过程"})
    if process_matches:
        issues.append({"code": "platform_process", "severity": "error", "message": f"正文包含 {process_matches} 处平台执行说明"})
    sentences = [re.sub(r"\s+", "", item) for item in re.split(r"[。！？\n]+", text) if len(re.sub(r"\s+", "", item)) >= 18]
    duplicate_count = sum(count - 1 for count in Counter(sentences).values() if count > 1)
    duplicate_ratio = duplicate_count / max(1, len(sentences))
    if duplicate_ratio > 0.05:
        issues.append({"code": "duplicate_content", "severity": "error", "message": f"重复长句比例 {duplicate_ratio:.1%}，超过 5%"})
    node_types = Counter(str(node.get("type") or "") for node in walk_plate_nodes(content))
    if expected_computation_count and node_types["computed_metric"] < expected_computation_count:
        issues.append({"code": "missing_computation_nodes", "severity": "error", "message": "确定性计算结果未完整写入正文"})
    if expected_inference_count and node_types["inference_conclusion"] < expected_inference_count:
        issues.append({"code": "missing_inference_nodes", "severity": "error", "message": "规则推演结论未完整写入正文"})
    if node_types["knowledge_citation"] == 0:
        issues.append({"code": "missing_citations", "severity": "error", "message": "正文没有可核验的知识引用"})
    trusted_ids = {
        str(node.get("id") or "")
        for node in walk_plate_nodes(content)
        if str(node.get("type") or "") in TRUSTED_BLOCK_TYPES
    }
    missing_bindings = sorted(block_id for block_id in trusted_ids if block_id and block_id not in bindings)
    if missing_bindings:
        issues.append({"code": "missing_binding", "severity": "error", "message": f"{len(missing_bindings)} 个可信内容缺少来源绑定"})
    length_ratio = None
    if reference_characters:
        length_ratio = len(text) / max(1, reference_characters)
        if length_ratio < 0.75 or length_ratio > 1.40:
            issues.append({"code": "length_out_of_range", "severity": "error", "message": f"正文篇幅为参考样稿的 {length_ratio:.1%}，应在 75%—140%"})
    return {
        "ok": not any(item["severity"] == "error" for item in issues),
        "issues": issues,
        "metrics": {
            "characters": len(text),
            "chapter_occurrences": chapter_occurrences,
            "duplicate_sentence_count": duplicate_count,
            "duplicate_sentence_ratio": round(duplicate_ratio, 4),
            "agent_work_note_matches": work_matches,
            "platform_process_matches": process_matches,
            "computed_metric_nodes": node_types["computed_metric"],
            "inference_conclusion_nodes": node_types["inference_conclusion"],
            "knowledge_citation_nodes": node_types["knowledge_citation"],
            "length_ratio": round(length_ratio, 4) if length_ratio is not None else None,
        },
    }
