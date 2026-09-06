#!/usr/bin/env python3
"""Build the safe, deterministic multi-format Guolian demo knowledge package."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "demo" / "guolian"
OUTPUT = DEMO_DIR / "国联集团演示知识包.zip"
MEMBERS = (
    "集团本部采购实施细则（2025演示现行版）.md",
    "智慧流程中枢项目建设方案（演示版）.pdf",
    "智慧流程中枢项目周报（演示版）.docx",
    "供应商风险台账（演示版）.xlsx",
    "项目系统依赖关系（演示版）.json",
    "规则推演前提事实（演示版）.txt",
    "东方智造交付延期通知（演示版）.eml",
    "项目总体架构图（演示版）.png",
    "扫描采购审批单（演示版）.jpg",
    "智慧流程中枢项目例会（演示版）.mp3",
)


def build() -> None:
    missing = [name for name in MEMBERS if not (DEMO_DIR / name).is_file()]
    if missing:
        raise SystemExit(f"缺少演示文件：{', '.join(missing)}")

    with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name in MEMBERS:
            info = ZipInfo(name)
            info.date_time = (2026, 9, 6, 0, 0, 0)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (DEMO_DIR / name).read_bytes())

    with ZipFile(OUTPUT) as archive:
        names = archive.namelist()
        if names != list(MEMBERS):
            raise SystemExit("演示 ZIP 文件清单不匹配")
        for item in archive.infolist():
            parts = PurePosixPath(item.filename).parts
            if PurePosixPath(item.filename).is_absolute() or ".." in parts:
                raise SystemExit(f"ZIP 存在不安全路径：{item.filename}")
            if not item.flag_bits & 0x800:
                raise SystemExit(f"ZIP 文件名未标记 UTF-8：{item.filename}")

    print(f"created={OUTPUT} members={len(MEMBERS)} safe_utf8=true")


if __name__ == "__main__":
    build()
