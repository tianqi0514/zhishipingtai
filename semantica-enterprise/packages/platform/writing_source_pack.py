"""Deterministic, pinned-source selection for a writing chapter.

The selection is a knowledge-tool projection, not an inference or a model
answer.  It only groups already-published chunks by reviewable outline terms.
"""

from __future__ import annotations

from typing import Any


_CHAPTER_MATCH_TERMS = {
    "背景", "目的", "依据", "必要性", "现状", "目标", "范围", "原则",
    "建设", "主要", "内容", "规模", "需求", "功能", "方案", "条件",
    "实施", "进度", "组织", "管理", "投资", "估算", "资金", "效益",
    "风险", "环境", "节能", "结论", "建议", "保障", "附件",
}


def _fallback_terms(terms: list[str]) -> list[str]:
    """Return conservative business keywords for near-heading matching.

    Source selection must stay deterministic, but an outline title such as
    ``主要内容`` still needs to match a parsed heading named
    ``主要建设内容``.  We deliberately use a small, reviewable vocabulary
    instead of an embedding/model call so the selected chunks are reproducible.
    """
    values: list[str] = []
    for raw in terms:
        compact = "".join(str(raw).split())
        for item in _CHAPTER_MATCH_TERMS:
            if item in compact and item not in values:
                values.append(item)
    return values


def select_chapter_source_rows(
    rows: list[dict[str, Any]],
    terms: list[str],
    *,
    max_characters: int = 12000,
) -> tuple[list[dict[str, Any]], bool]:
    if not rows or not terms:
        return [], False
    clean_terms = [str(term).strip() for term in terms if len(str(term).strip()) >= 2][:20]
    if not clean_terms:
        return [], False
    chosen: set[int] = set()
    for term in clean_terms:
        matches = [index for index, row in enumerate(rows) if term in str(row.get("text") or "")]
        # Prefer an exact structural heading, then the earliest relevant
        # occurrence.  A term may occur in many later body paragraphs.
        matches.sort(key=lambda index: (
            0 if str(rows[index].get("text") or "").strip().endswith(term)
                 and len(str(rows[index].get("text") or "")) <= 100 else 1,
            int(rows[index].get("ordinal") or 0),
        ))
        for anchor in matches[:2]:
            for index in range(anchor, min(len(rows), anchor + 9)):
                chosen.add(index)
    if not chosen:
        keywords = _fallback_terms(clean_terms)
        scored: list[tuple[int, int]] = []
        for index, row in enumerate(rows):
            text = str(row.get("text") or "")
            score = sum(1 for keyword in keywords if keyword in text)
            if score:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], int(rows[item[1]].get("ordinal") or 0)))
        for _, anchor in scored[:3]:
            for index in range(anchor, min(len(rows), anchor + 5)):
                chosen.add(index)
    selected: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for index in sorted(chosen):
        row = rows[index]
        content = str(row.get("text") or "")
        if used + len(content) > max_characters:
            truncated = True
            break
        selected.append(row)
        used += len(content)
    return selected, truncated
