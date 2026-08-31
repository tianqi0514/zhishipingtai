from __future__ import annotations

import mimetypes
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .formats import supported_suffixes


class UnsafeFileError(ValueError):
    """Raised when a file fails a security or type-integrity check."""


@dataclass(frozen=True)
class FileIdentity:
    suffix: str
    detected_mime: str
    supplied_mime: str
    warnings: tuple[str, ...] = ()


_MAGIC_SIGNATURES: tuple[tuple[bytes, set[str], str], ...] = (
    (b"%PDF-", {".pdf"}, "application/pdf"),
    (b"PK\x03\x04", {".zip", ".docx", ".pptx", ".xlsx", ".xlsm", ".odt", ".ods", ".odp", ".epub"}, "application/zip"),
    (b"\x89PNG\r\n\x1a\n", {".png"}, "image/png"),
    (b"\xff\xd8\xff", {".jpg", ".jpeg"}, "image/jpeg"),
    (b"GIF87a", {".gif"}, "image/gif"),
    (b"GIF89a", {".gif"}, "image/gif"),
    (b"RIFF", {".wav", ".webp", ".avi"}, "application/riff"),
    (b"ID3", {".mp3"}, "audio/mpeg"),
    (b"fLaC", {".flac"}, "audio/flac"),
    (b"OggS", {".ogg"}, "audio/ogg"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", {".doc", ".ppt", ".xls", ".msg"}, "application/x-ole-storage"),
)


def _detected_signature(path: Path) -> tuple[set[str], str] | None:
    with path.open("rb") as stream:
        header = stream.read(32)
    for signature, suffixes, mime in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            if signature == b"RIFF" and len(header) >= 12:
                kind = header[8:12]
                if kind == b"WEBP":
                    return {".webp"}, "image/webp"
                if kind == b"WAVE":
                    return {".wav"}, "audio/wav"
                if kind == b"AVI ":
                    return {".avi"}, "video/x-msvideo"
            return suffixes, mime
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return {".mp4", ".mov", ".m4a"}, "video/mp4"
    return None


def validate_file_identity(path: Path, supplied_mime: str | None = None) -> FileIdentity:
    suffix = path.suffix.lower()
    if suffix not in supported_suffixes():
        raise UnsafeFileError(f"暂不支持的文件格式：{suffix or '无扩展名'}")
    supplied = (supplied_mime or "application/octet-stream").split(";", 1)[0].strip().lower()
    guessed = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").lower()
    signature = _detected_signature(path)
    warnings: list[str] = []
    detected = guessed
    if signature:
        expected_suffixes, detected = signature
        if suffix not in expected_suffixes:
            raise UnsafeFileError(f"文件内容与扩展名 {suffix} 不一致")
    elif suffix in {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".docx", ".pptx", ".xlsx"}:
        raise UnsafeFileError(f"文件内容不是有效的 {suffix} 文件")
    if supplied not in {"", "application/octet-stream", detected, guessed}:
        # Browser MIME reporting differs for Office and archives, therefore this
        # mismatch is auditable but the byte signature remains authoritative.
        warnings.append(f"上传 MIME {supplied} 与检测类型 {detected} 不一致")
    return FileIdentity(suffix=suffix, detected_mime=detected, supplied_mime=supplied, warnings=tuple(warnings))


@dataclass(frozen=True)
class ZipLimits:
    max_files: int = 200
    max_total_bytes: int = 512 * 1024 * 1024
    max_member_bytes: int = 100 * 1024 * 1024
    max_ratio: float = 100.0
    max_depth: int = 2


def _safe_member_name(raw_name: str) -> PurePosixPath:
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise UnsafeFileError(f"ZIP 包含不安全路径：{raw_name}")
    if any(part in {"", "."} for part in path.parts):
        raise UnsafeFileError(f"ZIP 包含无效路径：{raw_name}")
    return path


def safe_extract_zip(
    archive: Path,
    destination: Path,
    *,
    limits: ZipLimits | None = None,
    depth: int = 0,
) -> list[Path]:
    limits = limits or ZipLimits()
    if depth > limits.max_depth:
        raise UnsafeFileError("ZIP 递归层级超过限制")
    extracted: list[Path] = []
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        members = [item for item in bundle.infolist() if not item.is_dir()]
        if len(members) > limits.max_files:
            raise UnsafeFileError("ZIP 文件数量超过限制")
        for item in members:
            member = _safe_member_name(item.filename)
            if item.file_size > limits.max_member_bytes:
                raise UnsafeFileError(f"ZIP 成员过大：{item.filename}")
            total += item.file_size
            if total > limits.max_total_bytes:
                raise UnsafeFileError("ZIP 解压后总大小超过限制")
            ratio = item.file_size / max(item.compress_size, 1)
            if ratio > limits.max_ratio:
                raise UnsafeFileError(f"ZIP 压缩比异常：{item.filename}")
            target = destination.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            resolved = target.resolve()
            if os.path.commonpath([destination.resolve(), resolved]) != str(destination.resolve()):
                raise UnsafeFileError(f"ZIP 路径越界：{item.filename}")
            with bundle.open(item) as source, resolved.open("wb") as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
            extracted.append(resolved)
    return extracted


def allowed_archive_members(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if path.suffix.lower() in supported_suffixes()]
