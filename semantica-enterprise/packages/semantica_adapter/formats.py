from __future__ import annotations

from typing import Any


# This matrix is also exposed through the API and used by upload validation. It
# deliberately distinguishes a parser that is usable without a model from an
# optional enrichment that needs a tenant model configuration.
FORMAT_CAPABILITIES: dict[str, dict[str, Any]] = {
    **{
        suffix: {"family": "text", "parser": "text", "native": suffix in {".txt", ".md"}}
        for suffix in {
            ".txt", ".md", ".rst", ".log", ".yaml", ".yml", ".toml", ".ini", ".conf"
        }
    },
    **{
        suffix: {"family": "code", "parser": "semantica-code", "native": True}
        for suffix in {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".h", ".hpp",
            ".cs", ".rb", ".go", ".rs", ".php", ".sql", ".sh", ".bash", ".css"
        }
    },
    ".html": {"family": "web", "parser": "semantica-html", "native": True},
    ".htm": {"family": "web", "parser": "semantica-html", "native": True},
    ".csv": {"family": "structured", "parser": "semantica-csv", "native": True},
    ".json": {"family": "structured", "parser": "semantica-json", "native": True},
    ".jsonl": {"family": "structured", "parser": "jsonl-adapter", "native": False},
    ".xml": {"family": "structured", "parser": "semantica-xml", "native": True},
    ".parquet": {"family": "structured", "parser": "semantica-parquet", "native": True},
    ".arrow": {"family": "structured", "parser": "semantica-arrow", "native": True},
    ".feather": {"family": "structured", "parser": "semantica-arrow", "native": True},
    ".pdf": {"family": "document", "parser": "semantica-pdf/docling", "native": True, "ocr": True},
    ".docx": {"family": "document", "parser": "semantica-docx", "native": True},
    ".pptx": {"family": "document", "parser": "semantica-pptx", "native": True},
    ".xlsx": {"family": "document", "parser": "semantica-excel", "native": True},
    ".xlsm": {"family": "document", "parser": "semantica-excel", "native": True},
    **{
        suffix: {
            "family": "document", "parser": "libreoffice+semantica", "native": False,
            "system_dependency": "libreoffice-headless",
        }
        for suffix in {".doc", ".ppt", ".xls"}
    },
    **{
        suffix: {"family": "document", "parser": "odf-adapter", "native": False}
        for suffix in {".odt", ".ods", ".odp"}
    },
    ".rtf": {"family": "document", "parser": "rtf-adapter", "native": False},
    **{
        suffix: {"family": "image", "parser": "semantica-image", "native": True, "ocr": True, "vision_optional": True}
        for suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
    },
    ".eml": {"family": "email", "parser": "semantica-email", "native": True, "recursive_attachments": True},
    ".msg": {"family": "email", "parser": "msg-adapter", "native": False, "recursive_attachments": True},
    ".epub": {"family": "ebook", "parser": "epub-adapter", "native": False},
    **{
        suffix: {
            "family": "audio", "parser": "semantica-media", "native": True,
            "asr_required_for_transcript": True, "system_dependency": "ffmpeg",
        }
        for suffix in {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
    },
    **{
        suffix: {
            "family": "video", "parser": "semantica-media", "native": True,
            "asr_required_for_transcript": True, "vision_optional": True,
            "system_dependency": "ffmpeg",
        }
        for suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    },
    ".zip": {
        "family": "archive", "parser": "safe-zip-adapter", "native": False,
        "recursive": True,
    },
}


def supported_suffixes() -> set[str]:
    return set(FORMAT_CAPABILITIES)

