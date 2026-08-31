from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import shutil
import subprocess

from packages.domain import CapabilityItem, CapabilityReport
from packages.platform.config import get_settings


def _module_item(key: str, module: str, name: str, required_for: str) -> CapabilityItem:
    available = importlib.util.find_spec(module) is not None
    version = None
    detail = ""
    if available:
        try:
            version = importlib.metadata.version(module.replace("_", "-"))
        except importlib.metadata.PackageNotFoundError:
            try:
                version = getattr(importlib.import_module(module), "__version__", None)
            except Exception:
                version = None
    else:
        detail = f"缺少 Python 模块 {module}"
    return CapabilityItem(
        key=key,
        name=name,
        available=available,
        version=version,
        detail=detail,
        required_for=required_for,
    )


def _tesseract_item() -> CapabilityItem:
    executable = shutil.which("tesseract")
    available = executable is not None
    detail = ""
    version = None
    if executable:
        try:
            output = subprocess.run(
                [executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.splitlines()
            version = output[0].replace("tesseract ", "") if output else None
            languages = subprocess.run(
                [executable, "--list-langs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            if "chi_sim" not in languages:
                available = False
                detail = "Tesseract 已安装，但缺少 chi_sim 中文语言包"
        except Exception as exc:
            available = False
            detail = f"Tesseract 检测失败：{exc}"
    else:
        detail = "缺少 tesseract 可执行程序"
    return CapabilityItem(
        key="tesseract",
        name="中文 OCR",
        available=available,
        version=version,
        detail=detail,
        required_for="M4 扫描件与图片",
    )


def build_capability_report() -> CapabilityReport:
    settings = get_settings()
    try:
        semantica_version = importlib.metadata.version("semantica")
    except importlib.metadata.PackageNotFoundError:
        semantica_version = "not-installed"

    items = [
        CapabilityItem(
            key="semantica",
            name="Semantica",
            available=semantica_version == settings.semantica_required_version,
            version=semantica_version,
            detail=(
                ""
                if semantica_version == settings.semantica_required_version
                else f"要求 {settings.semantica_required_version}"
            ),
            required_for="M0-M4 核心",
        ),
        _module_item("pdf", "pdfplumber", "PDF 解析", "M4 PDF"),
        _module_item("docx", "docx", "Word 解析", "M4 DOCX"),
        _module_item("pptx", "pptx", "PPT 解析", "M4 PPTX"),
        _module_item("excel", "openpyxl", "Excel 解析", "M4 XLSX"),
        _module_item("docling", "docling", "Docling 布局解析", "M4 扫描 PDF/复杂版式"),
        _module_item("pytesseract", "pytesseract", "OCR Python 适配", "M4 图片 OCR"),
        _tesseract_item(),
        _module_item("pyshacl", "pyshacl", "SHACL", "后续 M8"),
        _module_item("falkordb", "falkordb", "FalkorDB Driver", "后续 M8"),
        _module_item("qdrant", "qdrant_client", "Qdrant Driver", "后续 M9"),
    ]
    m4_keys = {"semantica", "pdf", "docx", "pptx", "excel", "docling", "pytesseract", "tesseract"}
    ready = all(item.available for item in items if item.key in m4_keys)
    return CapabilityReport(
        semantica_version=semantica_version,
        ready_for_m4=ready,
        items=items,
    )

