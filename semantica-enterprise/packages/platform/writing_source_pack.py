"""Deterministic, pinned-source selection for a writing chapter.

The selection is a knowledge-tool projection, not an inference or a model
answer.  It only groups already-published chunks by reviewable outline terms.
"""

from __future__ import annotations

from typing import Any


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
