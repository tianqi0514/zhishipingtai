from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from packages.domain import ContentElementData

from .file_safety import ZipLimits, allowed_archive_members, safe_extract_zip, validate_file_identity
from .formats import supported_suffixes


MediaTranscriber = Callable[[Path, str], dict[str, Any]]
VisionDescriber = Callable[[Path, str], dict[str, Any]]


_CODE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".h", ".hpp",
    ".cs", ".rb", ".go", ".rs", ".php", ".sql", ".sh", ".bash", ".css",
}
_TEXT_SUFFIXES = {".txt", ".md", ".rst", ".log", ".yaml", ".yml", ".toml", ".ini", ".conf"}
_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _clean_control_characters(value: str) -> str:
    """Remove characters PostgreSQL cannot safely store in text/JSON fields."""
    return "".join(
        char
        for char in value
        if char in {"\n", "\r", "\t"} or ord(char) >= 32
    )


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {
            "omitted": True,
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, str):
        return _clean_control_characters(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean_control_characters(str(value))


def _stable_element_id(
    version_id: str,
    element_type: str,
    structural_path: str,
    ordinal: int,
    text: str,
) -> str:
    payload = "\x1f".join(
        [version_id, "content-element-v1", element_type, structural_path, str(ordinal), text]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _element(
    version_id: str,
    element_type: str,
    ordinal: int,
    text: str,
    structural_path: str,
    *,
    page_number: int | None = None,
    bbox: list[float] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ContentElementData:
    clean_text = (
        _clean_control_characters(text)
        if isinstance(text, str)
        else json.dumps(_jsonable(text), ensure_ascii=False)
    )
    return ContentElementData(
        element_id=_stable_element_id(
            version_id, element_type, structural_path, ordinal, clean_text
        ),
        element_type=element_type,
        ordinal=ordinal,
        text=clean_text,
        structural_path=structural_path,
        page_number=page_number,
        bbox=bbox,
        metadata=_jsonable(metadata or {}),
    )


def _pdf_text_needs_fallback(result: Any) -> bool:
    # Inspect the raw text before `_jsonable` sanitizes control characters.
    data = result if isinstance(result, dict) else _jsonable(result)
    text = str(data.get("full_text") or "")
    if not text:
        text = "\n".join(
            str(page.get("text") or page.get("content") or "")
            for page in (data.get("pages") or [])
        )
    if not text.strip():
        return True
    if "\x00" in text:
        return True
    # Custom/embedded PDF fonts can yield NUL-heavy pseudo text. In automatic
    # mode, route those files through Docling instead of persisting garbage.
    control_count = sum(
        1 for char in text if char not in {"\n", "\r", "\t"} and ord(char) < 32
    )
    return control_count / max(len(text), 1) >= 0.01


def _parse_native(
    path: Path,
    policy: dict[str, Any],
    *,
    quality_fallback: bool,
) -> tuple[Any, str]:
    suffix = path.suffix.lower()
    options = {
        "extract_tables": policy.get("extract_tables", True),
        "extract_images": policy.get("extract_images", False),
    }
    if suffix == ".pdf":
        from semantica.parse.pdf_parser import PDFParser

        result = PDFParser().parse(path, **options)
        if policy.get("enable_ocr", True) and (
            not str(result.get("full_text") or "").strip()
            or (quality_fallback and _pdf_text_needs_fallback(result))
        ):
            from semantica.parse.docling_parser import DoclingParser

            result = DoclingParser(enable_ocr=True).parse(path, **options)
            return result, "docling"
        return result, "pdf"
    if suffix == ".docx":
        from semantica.parse.docx_parser import DOCXParser

        return DOCXParser().parse(path, **options), "docx"
    if suffix == ".pptx":
        from semantica.parse.pptx_parser import PPTXParser

        return PPTXParser().parse(path, **options), "pptx"
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        from semantica.parse.excel_parser import ExcelParser

        return ExcelParser().parse(path), "excel"
    if suffix == ".csv":
        from semantica.parse.csv_parser import CSVParser

        return CSVParser().parse(path), "csv"
    if suffix == ".json":
        from semantica.parse.json_parser import JSONParser

        return JSONParser().parse(path, extract_paths=True), "json"
    if suffix == ".jsonl":
        records = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"JSONL 第 {line_number} 行格式错误：{exc.msg}") from exc
        return {"data": records}, "json"
    if suffix == ".xml":
        from semantica.parse.xml_parser import XMLParser

        return XMLParser().parse(path), "xml"
    if suffix in {".html", ".htm"}:
        from semantica.parse.html_parser import HTMLParser

        return HTMLParser().parse(path, extract_tables=policy.get("extract_tables", True)), "html"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}:
        from semantica.parse.image_parser import ImageParser

        parser = ImageParser()
        # Semantica 0.6.6 ImageParser.parse() calls a misspelled private method
        # (_extract_metadata). Keep the workaround isolated in this version-pinned
        # adapter while still using Semantica's public metadata and OCR methods.
        metadata = parser.extract_metadata(path)
        ocr = None
        if policy.get("enable_ocr", True):
            ocr = parser.extract_text(
                path,
                language=policy.get("ocr_language", "chi_sim+eng"),
            )
        return {
            "metadata": _jsonable(metadata),
            "ocr": _jsonable(ocr),
            "text": ocr.text if ocr else "",
        }, "image"
    if suffix in _CODE_SUFFIXES:
        from semantica.parse.code_parser import CodeParser

        result = CodeParser().parse_code(path)
        result["full_text"] = path.read_text(encoding="utf-8", errors="replace")
        return result, "code"
    if suffix in _TEXT_SUFFIXES:
        return {"full_text": path.read_text(encoding="utf-8", errors="replace")}, "text"
    if suffix == ".eml":
        from semantica.parse.email_parser import EmailParser

        return EmailParser().parse_email(path, extract_attachments=True), "email"
    if suffix in _AUDIO_SUFFIXES | _VIDEO_SUFFIXES:
        from semantica.parse.media_parser import MediaParser

        media_type = "audio" if suffix in _AUDIO_SUFFIXES else "video"
        return {
            "type": media_type,
            "metadata": MediaParser().extract_metadata(path, media_type=media_type),
            "transcription_status": "not_configured",
        }, "media"
    if suffix == ".parquet":
        from semantica.ingest.parquet_ingestor import ParquetIngestor

        return ParquetIngestor().ingest(path, limit=policy.get("max_records")), "columnar"
    if suffix in {".arrow", ".feather"}:
        from semantica.ingest.arrow_ingestor import ArrowIngestor

        return ArrowIngestor().ingest(path, limit=policy.get("max_records")), "columnar"
    if suffix in {".odt", ".odp", ".ods"}:
        return _parse_odf(path), "odf"
    if suffix == ".rtf":
        from striprtf.striprtf import rtf_to_text

        return {"full_text": rtf_to_text(path.read_text(encoding="utf-8", errors="replace"))}, "rtf"
    if suffix == ".epub":
        return _parse_epub(path), "epub"
    if suffix == ".msg":
        return _parse_msg(path), "email"
    raise ValueError(f"暂不支持的文件格式：{suffix or '无扩展名'}")


def _parse_odf(path: Path) -> dict[str, Any]:
    from odf import teletype
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import H, P

    document = load(str(path))
    paragraphs = [teletype.extractText(node) for node in document.getElementsByType(P)]
    paragraphs += [teletype.extractText(node) for node in document.getElementsByType(H)]
    tables = []
    for table in document.getElementsByType(Table):
        rows = []
        for row in table.getElementsByType(TableRow):
            rows.append([teletype.extractText(cell) for cell in row.getElementsByType(TableCell)])
        tables.append({"name": table.getAttribute("name") or "", "rows": rows})
    return {"full_text": "\n".join(item for item in paragraphs if item.strip()), "tables": tables}


def _parse_epub(path: Path) -> dict[str, Any]:
    from bs4 import BeautifulSoup
    from ebooklib import ITEM_DOCUMENT, epub

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    sections = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        text = BeautifulSoup(item.get_content(), "html.parser").get_text("\n", strip=True)
        if text:
            sections.append({"name": item.get_name(), "text": text})
    return {"full_text": "\n\n".join(section["text"] for section in sections), "sections": sections}


def _parse_msg(path: Path) -> dict[str, Any]:
    import extract_msg

    message = extract_msg.Message(str(path))
    try:
        return {
            "headers": {
                "subject": message.subject,
                "from_address": message.sender,
                "to_addresses": [message.to] if message.to else [],
                "cc_addresses": [message.cc] if message.cc else [],
                "date": str(message.date or ""),
            },
            "body": {
                "text": message.body or "",
                "html": "",
                "attachments": [
                    {
                        "filename": attachment.longFilename or attachment.shortFilename or "attachment",
                        "content_type": getattr(attachment, "mimetype", None),
                        "size": len(attachment.data or b""),
                        "data": attachment.data or b"",
                    }
                    for attachment in message.attachments
                ],
            },
            "metadata": {"attachment_count": len(message.attachments)},
        }
    finally:
        message.close()


def _convert_legacy_office(path: Path, destination: Path) -> Path:
    target_by_suffix = {".doc": "docx", ".ppt": "pptx", ".xls": "xlsx"}
    target = target_by_suffix[path.suffix.lower()]
    command = [
        "libreoffice", "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
        "--convert-to", target, "--outdir", str(destination), str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    converted = destination / f"{path.stem}.{target}"
    if result.returncode != 0 or not converted.exists():
        detail = (result.stderr or result.stdout or "转换未生成输出")[-1000:]
        raise ValueError(f"旧版 Office 文件转换失败：{detail}")
    return converted


def _parse_docling(path: Path, policy: dict[str, Any]) -> tuple[Any, str]:
    from semantica.parse.docling_parser import DoclingParser

    parser = DoclingParser(enable_ocr=policy.get("enable_ocr", True))
    return (
        parser.parse(
            path,
            extract_tables=policy.get("extract_tables", True),
            extract_images=policy.get("extract_images", False),
        ),
        "docling",
    )


def _elements_from_result(
    version_id: str, parser_name: str, raw_result: Any
) -> list[ContentElementData]:
    result = _jsonable(raw_result)
    elements: list[ContentElementData] = []

    def add(
        element_type: str,
        text: Any,
        path: str,
        *,
        page: int | None = None,
        metadata: dict[str, Any] | None = None,
        bbox: list[float] | None = None,
    ) -> None:
        clean = (
            _clean_control_characters(text)
            if isinstance(text, str)
            else json.dumps(_jsonable(text), ensure_ascii=False)
        )
        if not clean.strip() and element_type not in {"image"}:
            return
        elements.append(
            _element(
                version_id,
                element_type,
                len(elements),
                clean,
                path,
                page_number=page,
                metadata={"parser": parser_name, **(metadata or {})},
                bbox=bbox,
            )
        )

    if parser_name in {"pdf", "docling"}:
        for index, page in enumerate(result.get("pages") or [], 1):
            page_no = int(page.get("page_number") or page.get("page_no") or index)
            add(
                "paragraph",
                page.get("text") or page.get("content") or "",
                f"pages/{page_no}",
                page=page_no,
                metadata={key: value for key, value in page.items() if key not in {"text", "content"}},
            )
        for index, table in enumerate(result.get("tables") or []):
            page_no = table.get("page_number") or table.get("page_no")
            add("table", table, f"tables/{index}", page=page_no, metadata={"table_index": index})
        if not elements:
            add("text", result.get("full_text", ""), "document")
    elif parser_name == "docx":
        for index, paragraph in enumerate(result.get("paragraphs") or []):
            add("paragraph", paragraph, f"paragraphs/{index}")
        for index, table in enumerate(result.get("tables") or []):
            add("table", table, f"tables/{index}", metadata={"table_index": index})
    elif parser_name == "pptx":
        for slide in result.get("slides") or []:
            number = int(slide.get("slide_number") or len(elements) + 1)
            title = slide.get("title") or ""
            body = slide.get("text") or ""
            notes = slide.get("notes") or ""
            add(
                "slide",
                "\n".join(value for value in [title, body, notes] if value),
                f"slides/{number}",
                page=number,
                metadata={"title": title, "notes": notes},
            )
    elif parser_name == "excel":
        sheets = result.get("sheets") or {}
        if isinstance(sheets, list):
            sheets = {str(index): sheet for index, sheet in enumerate(sheets)}
        for name, sheet in sheets.items():
            add(
                "sheet",
                sheet.get("data") or sheet,
                f"sheets/{name}",
                metadata={"sheet_name": name, "headers": sheet.get("headers", [])},
            )
    elif parser_name == "csv":
        for index, row in enumerate(result.get("rows") or []):
            add("record", row, f"rows/{index}", metadata={"row_number": index + 1})
    elif parser_name == "json":
        data = result.get("data")
        rows = data if isinstance(data, list) else [data]
        for index, row in enumerate(rows):
            add("record", row, f"items/{index}")
    elif parser_name == "xml":
        def walk(node: dict[str, Any], path: str) -> None:
            add(
                "record",
                node.get("text") or "",
                path,
                metadata={"tag": node.get("tag"), "attributes": node.get("attributes", {})},
            )
            for index, child in enumerate(node.get("children") or []):
                walk(child, f"{path}/{child.get('tag', 'node')}[{index}]")

        root = result.get("root") or {}
        walk(root, f"/{root.get('tag', 'root')}")
    elif parser_name == "html":
        add("text", result.get("text") or "", "document", metadata={"links": result.get("links", [])})
        for index, table in enumerate(result.get("tables") or []):
            add("table", table, f"tables/{index}")
    elif parser_name == "image":
        metadata = result.get("metadata") or {}
        text = "\n\n".join(
            item for item in [result.get("text") or "", result.get("vision_description") or ""] if item
        )
        add(
            "image",
            text,
            "image/0",
            metadata={
                "image": metadata,
                "ocr": result.get("ocr"),
                "vision_status": result.get("vision_status", "not_configured"),
                "vision_model": result.get("vision_model"),
            },
        )
    elif parser_name == "code":
        add(
            "code",
            result.get("full_text") or "",
            "code/0",
            metadata={
                "language": result.get("language"),
                "line_count": result.get("line_count"),
                "structure": result.get("structure", {}),
                "comments": result.get("comments", []),
                "dependencies": result.get("dependencies", {}),
            },
        )
    elif parser_name == "email":
        headers = result.get("headers") or {}
        body = result.get("body") or {}
        text = body.get("text") or ""
        if not text and body.get("html"):
            from bs4 import BeautifulSoup

            text = BeautifulSoup(body["html"], "html.parser").get_text("\n", strip=True)
        heading = "\n".join(
            value
            for value in [
                f"主题：{headers.get('subject')}" if headers.get("subject") else "",
                f"发件人：{headers.get('from_address')}" if headers.get("from_address") else "",
                f"收件人：{', '.join(headers.get('to_addresses') or [])}" if headers.get("to_addresses") else "",
                f"日期：{headers.get('date')}" if headers.get("date") else "",
            ]
            if value
        )
        add(
            "email",
            "\n\n".join(value for value in [heading, text] if value),
            "email/body",
            metadata={"headers": headers, "message": result.get("metadata", {})},
        )
        for index, attachment in enumerate(body.get("attachments") or []):
            add(
                "attachment",
                attachment.get("filename") or f"附件 {index + 1}",
                f"email/attachments/{index}",
                metadata={key: value for key, value in attachment.items() if key != "data"},
            )
    elif parser_name == "media":
        media_type = result.get("type") or "audio"
        metadata = result.get("metadata") or {}
        transcript = result.get("transcript") or ""
        add(media_type, json.dumps(metadata, ensure_ascii=False), f"{media_type}/metadata", metadata=metadata)
        if transcript:
            add(
                "transcript",
                transcript,
                f"{media_type}/transcript",
                metadata={
                    "segments": result.get("segments") or [],
                    "model": result.get("model"),
                    "transcription_status": result.get("transcription_status", "succeeded"),
                },
            )
        if media_type == "video" and result.get("vision_description"):
            add(
                "visual_description",
                result["vision_description"],
                "video/keyframes",
                metadata={
                    "keyframes": result.get("keyframes") or [],
                    "model": result.get("vision_model"),
                    "vision_status": result.get("vision_status", "succeeded"),
                },
            )
    elif parser_name == "columnar":
        for index, row in enumerate(result.get("data") or []):
            add("record", row, f"rows/{index}", metadata={"row_number": index + 1})
        if not elements:
            add("record", result.get("schema") or result.get("metadata") or {}, "schema")
    elif parser_name == "odf":
        add("text", result.get("full_text") or "", "document")
        for index, table in enumerate(result.get("tables") or []):
            add("table", table, f"tables/{index}")
    elif parser_name == "epub":
        for index, section in enumerate(result.get("sections") or []):
            add("paragraph", section.get("text") or "", f"sections/{index}", metadata={"name": section.get("name")})
    elif parser_name == "rtf":
        add("text", result.get("full_text") or "", "document")
    else:
        add("text", result.get("full_text") or result.get("text") or "", "document")

    if not elements:
        raise ValueError("解析器未产生可用内容元素")
    return elements


def parse_document(
    path: Path,
    *,
    version_id: str,
    policy: dict[str, Any],
    supplied_mime: str | None = None,
    media_transcriber: MediaTranscriber | None = None,
    visual_describer: VisionDescriber | None = None,
) -> tuple[list[ContentElementData], dict[str, Any]]:
    identity = validate_file_identity(path, supplied_mime)
    suffix = path.suffix.lower()
    if suffix in {".doc", ".ppt", ".xls"}:
        with tempfile.TemporaryDirectory(prefix="semantica-office-") as temporary_directory:
            converted = _convert_legacy_office(path, Path(temporary_directory))
            elements, summary = parse_document(
                converted,
                version_id=version_id,
                policy=policy,
                media_transcriber=media_transcriber,
                visual_describer=visual_describer,
            )
        summary.update(
            {
                "parser": f"libreoffice+{summary['parser']}",
                "converted_from": suffix,
                "mime_warnings": list(identity.warnings),
            }
        )
        return elements, summary

    if suffix == ".zip":
        return _parse_archive(
            path,
            version_id=version_id,
            policy=policy,
            identity_warnings=list(identity.warnings),
            media_transcriber=media_transcriber,
            visual_describer=visual_describer,
        )

    parser_type = policy.get("parser_type", "auto")
    if parser_type == "docling":
        raw_result, parser_name = _parse_docling(path, policy)
    else:
        raw_result, parser_name = _parse_native(
            path,
            policy,
            quality_fallback=parser_type == "auto",
        )

    if parser_name == "media":
        media_type = raw_result.get("type") or "audio"
        if media_transcriber:
            try:
                raw_result.update(media_transcriber(path, media_type))
                raw_result.setdefault("transcription_status", "succeeded")
            except Exception as exc:
                raw_result["transcription_status"] = "failed"
                raw_result["transcription_error"] = f"{type(exc).__name__}: {exc}"[:1000]

    if parser_name == "image" or (parser_name == "media" and raw_result.get("type") == "video"):
        raw_result.setdefault("vision_status", "not_configured")
        if visual_describer:
            try:
                raw_result.update(
                    visual_describer(path, "video" if parser_name == "media" else "image")
                )
                raw_result.setdefault("vision_status", "succeeded")
            except Exception as exc:
                raw_result["vision_status"] = "failed"
                raw_result["vision_error"] = f"{type(exc).__name__}: {exc}"[:1000]

    attachment_results: list[tuple[str, list[ContentElementData], dict[str, Any]]] = []
    if parser_name == "email" and policy.get("parse_email_attachments", True):
        for index, attachment in enumerate(_email_attachment_payloads(raw_result)):
            filename = Path(str(attachment.get("filename") or f"attachment-{index}")).name
            attachment_suffix = Path(filename).suffix.lower()
            data = attachment.get("data")
            if not isinstance(data, bytes) or attachment_suffix not in supported_suffixes():
                continue
            with tempfile.TemporaryDirectory(prefix="semantica-email-") as temporary_directory:
                attachment_path = Path(temporary_directory) / filename
                attachment_path.write_bytes(data)
                try:
                    child_elements, child_summary = parse_document(
                        attachment_path,
                        version_id=version_id,
                        policy={**policy, "parse_email_attachments": False},
                        supplied_mime=attachment.get("content_type"),
                        media_transcriber=media_transcriber,
                        visual_describer=visual_describer,
                    )
                    attachment_results.append((filename, child_elements, child_summary))
                except Exception as exc:
                    attachment_results.append(
                        (
                            filename,
                            [],
                            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:1000]},
                        )
                    )

    elements = _elements_from_result(version_id, parser_name, raw_result)
    attachment_summaries = []
    for attachment_index, (filename, child_elements, child_summary) in enumerate(attachment_results):
        attachment_summaries.append({"filename": filename, **child_summary})
        for child in child_elements:
            structural_path = f"email/attachments/{attachment_index}/{filename}/{child.structural_path}"
            elements.append(
                _element(
                    version_id,
                    child.element_type,
                    len(elements),
                    child.text,
                    structural_path,
                    page_number=child.page_number,
                    bbox=child.bbox,
                    metadata={**child.metadata, "attachment_parent": filename},
                )
            )
    max_pages = policy.get("max_pages")
    if max_pages:
        elements = [item for item in elements if item.page_number is None or item.page_number <= int(max_pages)]
        if not elements:
            raise ValueError("所设最大页数范围内没有可用内容元素")
    total_chars = sum(len(item.text) for item in elements)
    summary = {
        "parser": parser_name,
        "element_count": len(elements),
        "text_chars": total_chars,
        "types": {
            element_type: sum(1 for item in elements if item.element_type == element_type)
            for element_type in sorted({item.element_type for item in elements})
        },
        "detected_mime": identity.detected_mime,
        "mime_warnings": list(identity.warnings),
    }
    if parser_name == "media":
        summary["transcription_status"] = raw_result.get("transcription_status")
        if raw_result.get("transcription_error"):
            summary["warnings"] = [raw_result["transcription_error"]]
    if parser_name == "image" or (parser_name == "media" and raw_result.get("type") == "video"):
        summary["vision_status"] = raw_result.get("vision_status", "not_configured")
        if raw_result.get("vision_error"):
            summary.setdefault("warnings", []).append(raw_result["vision_error"])
    if attachment_summaries:
        summary["attachments"] = attachment_summaries
    return elements, summary


def _email_attachment_payloads(raw_result: Any) -> list[dict[str, Any]]:
    if dataclasses.is_dataclass(raw_result):
        body = getattr(raw_result, "body", None)
        return list(getattr(body, "attachments", []) or [])
    if isinstance(raw_result, dict):
        return list((raw_result.get("body") or {}).get("attachments") or [])
    return []


def _parse_archive(
    path: Path,
    *,
    version_id: str,
    policy: dict[str, Any],
    identity_warnings: list[str],
    media_transcriber: MediaTranscriber | None,
    visual_describer: VisionDescriber | None,
) -> tuple[list[ContentElementData], dict[str, Any]]:
    depth = int(policy.get("_archive_depth", 0))
    limits = ZipLimits(
        max_files=int(policy.get("zip_max_files", 200)),
        max_total_bytes=int(policy.get("zip_max_total_bytes", 512 * 1024 * 1024)),
        max_member_bytes=int(policy.get("zip_max_member_bytes", 100 * 1024 * 1024)),
        max_ratio=float(policy.get("zip_max_ratio", 100.0)),
        max_depth=int(policy.get("zip_max_depth", 2)),
    )
    if depth > limits.max_depth:
        raise ValueError("ZIP 递归层级超过限制")
    elements: list[ContentElementData] = []
    members: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="semantica-zip-") as temporary_directory:
        root = Path(temporary_directory)
        extracted = safe_extract_zip(path, root, limits=limits, depth=depth)
        for member in allowed_archive_members(extracted):
            relative = member.relative_to(root).as_posix()
            try:
                child_elements, child_summary = parse_document(
                    member,
                    version_id=version_id,
                    policy={**policy, "_archive_depth": depth + 1},
                    media_transcriber=media_transcriber,
                    visual_describer=visual_describer,
                )
                members.append({"path": relative, "status": "succeeded", **child_summary})
                for child in child_elements:
                    structural_path = f"archive/{relative}/{child.structural_path}"
                    elements.append(
                        _element(
                            version_id,
                            child.element_type,
                            len(elements),
                            child.text,
                            structural_path,
                            page_number=child.page_number,
                            bbox=child.bbox,
                            metadata={**child.metadata, "archive_member": relative},
                        )
                    )
            except Exception as exc:
                members.append(
                    {"path": relative, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:1000]}
                )
    if not elements:
        raise ValueError("ZIP 中没有可成功解析的受支持文件")
    return elements, {
        "parser": "safe-zip",
        "element_count": len(elements),
        "text_chars": sum(len(item.text) for item in elements),
        "types": {
            element_type: sum(1 for item in elements if item.element_type == element_type)
            for element_type in sorted({item.element_type for item in elements})
        },
        "members": members,
        "mime_warnings": identity_warnings,
    }
