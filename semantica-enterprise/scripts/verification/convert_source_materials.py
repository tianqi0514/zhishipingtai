"""Convert authorised source documents without modifying their originals.

DOCX body order and table contents are retained; PDF page locators are explicit.
This is an offline import aid, not a substitute for the platform parser.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def convert_docx(source: Path) -> tuple[str, dict]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(source)
    output = [f"# {source.stem}", "", f"原始文件：{source.name}", ""]
    paragraphs = tables = 0
    for element in doc.element.body:
        if element.tag.endswith("}p"):
            paragraph = Paragraph(element, doc)
            value = paragraph.text.strip()
            paragraphs += 1
            if not value:
                continue
            style = paragraph.style.name if paragraph.style else ""
            level = re.search(r"(?:Heading|标题)\s*(\d+)", style, re.I)
            prefix = "#" * min(6, int(level.group(1)) + 1) + " " if level else ""
            output += [f"<!-- 原文段落 {paragraphs}；样式 {style} -->", prefix + value, ""]
        elif element.tag.endswith("}tbl"):
            tables += 1
            table = Table(element, doc)
            rows = []
            seen_cells = set()
            for row in table.rows:
                values = []
                for cell in row.cells:
                    # python-docx repeats the same XML cell for merged positions.
                    # Keep its value once; separate cells with identical text remain.
                    values.append("" if cell._tc in seen_cells else cell.text.replace("|", "\\|").replace("\n", "<br>"))
                    seen_cells.add(cell._tc)
                rows.append(values)
            if not rows:
                continue
            output += [f"<!-- 原文表格 {tables} -->", "| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in rows[0]) + " |"]
            output += ["| " + " | ".join(row) + " |" for row in rows[1:]] + [""]
    images = sum(1 for rel in doc.part.rels.values() if "image" in rel.reltype)
    if images:
        output += [f"> 原文件含 {images} 个图片资源；文字与表格已转换，图片中的信息请核对原始 DOCX，不能据此声称图像已解析。", ""]
    return "\n".join(output), {"paragraphs": paragraphs, "tables": tables, "image_resources": images, "conversion": "docx_body_order"}


def convert_pdf(source: Path) -> tuple[str, dict]:
    from pypdf import PdfReader
    reader = PdfReader(source)
    pages = [(page.extract_text(extraction_mode="layout") or "").strip() for page in reader.pages]
    broken = [index + 1 for index, value in enumerate(pages) if len(re.findall(r"/G[0-9A-F]{2,4}", value)) > 10]
    empty = [index + 1 for index, value in enumerate(pages) if len(re.sub(r"\s", "", value)) < 30]
    output = [f"# {source.stem}", "", f"原始文件：{source.name}", "", "> 文本按页提取；图形、公式排版和表格须对照原 PDF 核验。", ""]
    for index, value in enumerate(pages):
        if index + 1 in broken:
            value = "[PDF 字体映射缺失，文本不可用；需要 OCR 或人工核对]"
        output += [f"## 原文第 {index + 1} 页", "", value or "[本页没有可提取的文本，需要 OCR 或人工核对]", ""]
    return "\n".join(output), {"pages": len(pages), "low_text_pages": empty, "broken_font_pages": broken, "conversion": "pdf_text_by_page", "requires_ocr": bool(empty or broken)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output already exists; original conversion is preserved")
    result, metrics = convert_docx(args.source) if args.source.suffix.lower() == ".docx" else convert_pdf(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8")
    audit = {"source": args.source.name, "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(), "output": args.output.name,
             "output_sha256": hashlib.sha256(result.encode()).hexdigest(), "characters": len(result), **metrics}
    args.output.with_suffix(".conversion.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
