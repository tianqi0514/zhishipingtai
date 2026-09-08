from __future__ import annotations

import json
import html
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "geojson": "application/geo+json",
}


@lru_cache(maxsize=1)
def _cjk_font_name() -> str:
    """Select an installed CJK font so server-side PDF rendering keeps Chinese text."""
    executable = shutil.which("fc-list")
    if executable:
        completed = subprocess.run(
            [executable, ":lang=zh", "family"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        available = completed.stdout.casefold()
        for candidate in (
            "Noto Sans CJK SC",
            "WenQuanYi Zen Hei",
            "Arial Unicode MS",
            "PingFang SC",
            "Hiragino Sans GB",
            "SimSun",
        ):
            if candidate.casefold() in available:
                return candidate
    return "Arial Unicode MS"


def _node_text(node: dict[str, Any]) -> str:
    if "text" in node:
        return str(node.get("text") or "")
    return "".join(_node_text(item) for item in (node.get("children") or []) if isinstance(item, dict))


def _clean_citation_text(value: str, max_length: int = 420) -> str:
    """Render retrieved Markdown as a compact, safe source excerpt."""
    cleaned = html.unescape(value)
    cleaned = re.sub(r"(^|[\s：])#{1,6}\s+", r"\1", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s{0,3}>\s?", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"(?:\*\*|__|`)(.*?)(?:\*\*|__|`)", r"\1", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_length:
        return cleaned[: max_length - 1].rstrip() + "…"
    return cleaned


def _set_run_font(run, size: float = 11, bold: bool = False) -> None:
    font_name = _cjk_font_name()
    run.font.name = font_name
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font_name)
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)


def _configure_docx(document: Document, title: str) -> None:
    section = document.sections[0]
    # Letter portrait is the deterministic default required by the document
    # production contract. The template layer may explicitly override it.
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.82)
    section.bottom_margin = Inches(0.78)
    section.left_margin = Inches(0.86)
    section.right_margin = Inches(0.78)
    styles = document.styles
    font_name = _cjk_font_name()
    for style_name, size, bold in (("Normal", 11, False), ("Title", 22, True), ("Heading 1", 16, True), ("Heading 2", 14, True), ("Heading 3", 12, True)):
        style = styles[style_name]
        style.font.name = font_name
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font_name)
        style.font.size = Pt(size)
        style.font.bold = bold
        style.font.color.rgb = RGBColor(0, 0, 0)
    title_paragraph = document.add_paragraph(style="Title")
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(title_paragraph.add_run(title), 22, True)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(subtitle.add_run("应急处置方案"), 12, False)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(header.add_run(title), 9, False)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("第 ")
    _set_run_font(run, 9, False)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    run._r.addnext(field)
    tail = footer.add_run(" 页")
    _set_run_font(tail, 9, False)


def _add_table(document: Document, node: dict[str, Any]) -> None:
    rows = [item for item in (node.get("children") or []) if isinstance(item, dict)]
    matrix = []
    for row in rows:
        cells = [item for item in (row.get("children") or []) if isinstance(item, dict)]
        matrix.append([_node_text(cell).strip() for cell in cells])
    width = max((len(row) for row in matrix), default=0)
    if not width:
        return
    table = document.add_table(rows=len(matrix), cols=width)
    table.style = "Table Grid"
    for row_index, values in enumerate(matrix):
        for column_index in range(width):
            cell = table.cell(row_index, column_index)
            cell.text = values[column_index] if column_index < len(values) else ""
            cell.vertical_alignment = 1
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if row_index == 0 else WD_ALIGN_PARAGRAPH.LEFT
                for run in paragraph.runs:
                    _set_run_font(run, 10, row_index == 0)


def build_docx(path: Path, *, title: str, content: list[dict[str, Any]], audit_summary: dict[str, Any]) -> None:
    document = Document()
    _configure_docx(document, title)
    opening = document.add_paragraph()
    _set_run_font(opening.add_run("本方案依据已锁定的知识版本、已核验事实、确定性计算和规则推演结果形成。发布前须完成业务确认。"), 11)
    for node in content:
        node_type = str(node.get("type") or "p")
        text = _node_text(node).strip()
        if node_type == "knowledge_citation":
            text = _clean_citation_text(text)
        if node_type == "table":
            _add_table(document, node)
            continue
        if not text:
            continue
        if node_type in {"h1", "heading1"}:
            paragraph = document.add_paragraph(style="Heading 1")
        elif node_type in {"h2", "heading2"}:
            paragraph = document.add_paragraph(style="Heading 2")
        elif node_type in {"h3", "heading3"}:
            paragraph = document.add_paragraph(style="Heading 3")
        elif node_type in {"ul", "bulleted-list"}:
            paragraph = document.add_paragraph(style="List Bullet")
        elif node_type in {"ol", "numbered-list"}:
            paragraph = document.add_paragraph(style="List Number")
        else:
            paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(7)
        paragraph.paragraph_format.line_spacing = 1.45
        prefix = ""
        if node_type == "knowledge_citation":
            prefix = "来源依据  "
        elif node_type == "computed_metric":
            prefix = "计算结果  "
        elif node_type == "inference_conclusion":
            prefix = "推演结论  "
        elif node_type == "manual_assumption":
            prefix = "人工假设  "
        if prefix:
            _set_run_font(paragraph.add_run(prefix), 10, True)
        _set_run_font(paragraph.add_run(text), 11, False)

    document.add_section(WD_SECTION.NEW_PAGE)
    heading = document.add_paragraph(style="Heading 1")
    _set_run_font(heading.add_run("生成依据摘要"), 16, True)
    summary_rows = [
        ("知识产品版本", str(audit_summary.get("knowledge_product_release") or "-")),
        ("场景包版本", str(audit_summary.get("scenario_package_version") or "-")),
        ("已核验事实", str(audit_summary.get("verified_fact_count") or 0)),
        ("确定性计算", str(audit_summary.get("computation_count") or 0)),
        ("规则推演", str(audit_summary.get("reasoning_count") or 0)),
        ("采用方案", str(audit_summary.get("selected_plan") or "未选择")),
    ]
    table = document.add_table(rows=len(summary_rows) + 1, cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "当前值"
    for index, (label, value) in enumerate(summary_rows, 1):
        table.rows[index].cells[0].text = label
        table.rows[index].cells[1].text = value
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    _set_run_font(run, 10, row_index == 0)
    document.save(path)


def build_xlsx(path: Path, *, facts: list[dict[str, Any]], computations: list[dict[str, Any]], plans: list[dict[str, Any]]) -> None:
    workbook = Workbook()
    header_fill = PatternFill("solid", fgColor="17365D")
    header_font = Font(color="FFFFFF", bold=True)

    facts_sheet = workbook.active
    facts_sheet.title = "已核验事实"
    facts_sheet.append(["事实", "值", "单位", "来源", "版本", "状态"])
    for item in facts:
        value = item.get("value") or {}
        facts_sheet.append([item.get("label"), value.get("number", value.get("text", value.get("value"))), item.get("unit"), item.get("source_type"), item.get("version"), item.get("verification_status")])

    computation_sheet = workbook.create_sheet("确定性计算")
    computation_sheet.append(["计算项", "结果", "单位", "公式", "输入事实"])
    for item in computations:
        result = item.get("result") or {}
        output = result.get("output_fact") or {}
        computation_sheet.append([output.get("label") or result.get("operation"), result.get("value"), output.get("unit"), result.get("operation"), "、".join((result.get("dependencies") or {}).values())])

    plan_sheet = workbook.create_sheet("备选方案")
    plan_sheet.append(["方案", "优化目标", "路线", "预计分钟", "路线风险", "状态"])
    for item in plans:
        route = (item.get("result") or {}).get("route") or {}
        plan_sheet.append([item.get("name"), item.get("objective"), " → ".join(route.get("path") or []), route.get("minutes"), route.get("risk"), item.get("status")])

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in sheet.columns:
            width = min(42, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
            sheet.column_dimensions[column[0].column_letter].width = width
    workbook.save(path)


def build_geojson(path: Path, *, selected_plan: dict[str, Any], coordinates: dict[str, list[float]]) -> None:
    route = (selected_plan.get("result") or {}).get("route") or {}
    nodes = route.get("path") or []
    missing = [node for node in nodes if node not in coordinates]
    if not nodes or missing:
        raise ValueError("采用方案缺少可核验的路线坐标")
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": selected_plan.get("name"),
                    "objective": selected_plan.get("objective"),
                    "minutes": route.get("minutes"),
                    "risk": route.get("risk"),
                },
                "geometry": {"type": "LineString", "coordinates": [coordinates[node] for node in nodes]},
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_export_artifact(
    path: Path,
    *,
    output_format: str,
    title: str,
    content: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    computations: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    audit_summary: dict[str, Any],
    coordinates: dict[str, list[float]] | None = None,
) -> Path:
    if output_format == "docx":
        build_docx(path, title=title, content=content, audit_summary=audit_summary)
    elif output_format == "pdf":
        source = path.with_suffix(".docx")
        build_docx(source, title=title, content=content, audit_summary=audit_summary)
        executable = shutil.which("libreoffice") or shutil.which("soffice")
        if executable is None:
            raise RuntimeError("服务器未安装 LibreOffice，无法生成 PDF")
        completed = subprocess.run(
            [executable, "--headless", "--convert-to", "pdf", "--outdir", str(path.parent), str(source)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        source.unlink(missing_ok=True)
        if completed.returncode != 0 or not path.exists():
            raise RuntimeError("LibreOffice 未能生成 PDF")
    elif output_format == "json":
        payload = {"title": title, "content": content, "facts": facts, "computations": computations, "plans": plans, "audit_summary": audit_summary}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    elif output_format == "xlsx":
        build_xlsx(path, facts=facts, computations=computations, plans=plans)
    elif output_format == "geojson":
        selected = next((item for item in plans if item.get("status") == "selected"), None)
        if selected is None:
            raise ValueError("请先选择一套备选方案")
        build_geojson(path, selected_plan=selected, coordinates=coordinates or {})
    else:
        raise ValueError("不支持的导出格式")
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("导出文件为空")
    return path
