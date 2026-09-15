"""Derive a reviewable article structure from a *pinned* sample document.

This is layout/style extraction, not fact extraction. No names, numbers,
administrative decisions or formulas from a sample become project facts.
"""

from __future__ import annotations

import re
from typing import Any


NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:[.．]\d+){0,3})\s+([^。；:：]{2,60})\s*$")
CHINESE_HEADING = re.compile(r"^\s*([一二三四五六七八九十]+)[、．.]\s*([^。；:：]{2,60})\s*$")
ATTACHMENT_HEADING = re.compile(r"^\s*附件\s*([一二三四五六七八九十\d]+)\s*(.{0,80})$")


def _headings(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in text.replace("\u3000", " ").splitlines():
        match = NUMBERED_HEADING.match(line) or CHINESE_HEADING.match(line)
        if not match:
            continue
        code, title = match.group(1).replace("．", "."), match.group(2).strip()
        if len(title) < 2 or len(title) > 56 or title.isdigit():
            continue
        rows.append((code, title))
    return rows


def sample_main_body_lengths(text: str, chapters: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure numbered article body, excluding notice/contents/attachments.

    This extracts only layout lengths, never facts from the sample.  A repeated
    first chapter heading indicates a contents page followed by the body.
    """
    if not chapters:
        return {}
    positions: list[tuple[int, str]] = []
    attachments: list[int] = []
    offset = 0
    def chapter_number(item: dict[str, Any]) -> str:
        return str(item.get("source_number") or str(item.get("key") or "").removeprefix("sample-section-"))

    expected = {chapter_number(item): str(item["title"]) for item in chapters}
    for line in text.splitlines(keepends=True):
        heading = NUMBERED_HEADING.match(line.strip())
        if heading and heading.group(1) in expected and heading.group(2).strip() == expected[heading.group(1)]:
            positions.append((offset, heading.group(1)))
        if ATTACHMENT_HEADING.match(line.strip()):
            attachments.append(offset)
        offset += len(line)
    first_code = chapter_number(chapters[0])
    first_positions = [position for position, code in positions if code == first_code]
    if not first_positions:
        return {}
    start = first_positions[-1] if len(first_positions) > 1 else first_positions[0]
    end = next((position for position in attachments if position > start), len(text))
    if end <= start:
        return {}
    body = text[start:end]
    total = len(re.sub(r"\s|\x0c", "", body))
    full = len(re.sub(r"\s|\x0c", "", text))
    if total < 300 or total > full:
        return {}
    section_positions = []
    for chapter in chapters:
        code = chapter_number(chapter)
        section_start = next((position for position, found_code in positions
                              if found_code == code and start <= position < end), None)
        if section_start is not None:
            section_positions.append((section_start, str(chapter["key"])))
    section_positions.sort()
    section_lengths = {}
    for index, (section_start, key) in enumerate(section_positions):
        section_end = section_positions[index + 1][0] if index + 1 < len(section_positions) else end
        section_lengths[key] = len(re.sub(r"\s|\x0c", "", text[section_start:section_end]))
    return {
        "reference_body_characters": total,
        "reference_section_characters": section_lengths,
        "body_boundary_method": "numbered-chapter-to-first-attachment-v1",
    }


def extract_sample_profile(text: str, *, version_id: str, source_sha256: str) -> dict[str, Any]:
    """Produce a bounded profile which users must confirm before generation."""
    if len(text.strip()) < 300:
        raise ValueError("样稿可读内容不足，无法提取文章结构")
    found = _headings(text)
    chapter_titles: dict[str, str] = {}
    child_titles: dict[str, list[str]] = {}
    for code, title in found:
        if code.isdigit():
            chapter_titles.setdefault(code, title)
        elif "." in code:
            parent = code.split(".", 1)[0]
            if code.count(".") == 1 and title not in child_titles.setdefault(parent, []):
                child_titles[parent].append(title)
    if not chapter_titles:
        raise ValueError("样稿未识别出一级标题，请人工指定目录")
    if len(chapter_titles) > 30:
        raise ValueError("样稿一级标题过多，需人工核对")
    chapters = []
    for code, title in chapter_titles.items():
        subheads = child_titles.get(code, [])[:20]
        focus = "、".join(subheads) if subheads else title
        chapters.append({
            "key": f"sample-section-{code}", "title": title, "source_number": code,
            "subheadings": subheads,
            "instruction": (
                f"围绕“{title}”撰写可交付的正式正文，按资料充分程度覆盖{focus}。"
                "逐项核对主体、职责、触发条件和执行动作；只引用本项目已授权、当前有效的依据。"
                "样稿仅提供结构与文风，不沿用其地区、文号、单位职责、数字或正式决定。"
            ),
            "generation_mode": "agent", "required_inputs": [], "toolbox_outputs": [],
            "citation_required": title in {"编制依据", "总则", "职责", "组织体系"},
        })
    attachments: list[dict[str, str]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = ATTACHMENT_HEADING.match(line)
        if match:
            number = match.group(1)
            if number not in {item["number"] for item in attachments}:
                title = match.group(2).strip()
                if not title:
                    title = next((candidate.strip() for candidate in lines[index + 1:index + 6]
                                  if 2 <= len(candidate.strip()) <= 80 and not candidate.strip().isdigit()), "")
                attachments.append({"number": number, "title": title or f"附件 {number}"})
    compact_front = re.sub(r"\s+", "", text[:8000])
    formal_notice = bool(
        re.search(r"关于印发.{2,100}的通知", compact_front)
        or re.search(r"现将.{2,100}印发给", compact_front[:1200])
    )
    clean_characters = len(re.sub(r"\s|\x0c", "", text))
    body_lengths = sample_main_body_lengths(text, chapters)
    return {
        "source_version_id": version_id, "source_sha256": source_sha256,
        "extraction_method": "numbered-layout-v1", "status": "draft",
        "genre": "formal_notice_and_plan" if formal_notice else "professional_report",
        "chapters": chapters, "attachments": attachments[:20],
        "style": {
            "register": "正式、审慎、职责和动作清楚",
            "heading_numbering": "multilevel" if any(child_titles.values()) else "single_level",
            "notice_page_detected": formal_notice,
            "notice_requires_authorized_signoff": formal_notice,
            "reference_characters": min(clean_characters, 30000),
            **body_lengths,
            "sample_is_not_factual_evidence": True,
        },
        "indicator_candidates": [],
        "formula_candidates": [],
        "warnings": [
            "样稿只用于目录与文风；正文事实、行政职责、响应等级和数值必须由新项目资料核验。",
            "样稿中没有可自动迁移的确定性公式；需要计算时另行确认输入和公式。",
        ],
    }


def validate_sample_profile(profile: dict[str, Any], *, source_version_id: str, source_sha256: str) -> list[dict[str, Any]]:
    if profile.get("source_version_id") != source_version_id or profile.get("source_sha256") != source_sha256:
        raise ValueError("样稿版本已变化，请重新提取配置")
    rows = profile.get("chapters")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise ValueError("请保留 1 至 30 个有效一级章节")
    chapters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("章节配置结构不正确")
        key = str(raw.get("key") or "").strip()
        title = str(raw.get("title") or "").strip()
        instruction = str(raw.get("instruction") or "").strip()
        if not key or key in seen or not title or len(title) > 100 or not instruction or len(instruction) > 2000:
            raise ValueError("章节编码、标题或写作要求无效或重复")
        seen.add(key)
        chapters.append({
            "key": key, "title": title, "instruction": instruction,
            "generation_mode": "agent", "required_inputs": [], "toolbox_outputs": [],
            "citation_required": raw.get("citation_required") is True,
            "subheadings": [str(item)[:100] for item in (raw.get("subheadings") or [])[:20]],
        })
    return chapters
