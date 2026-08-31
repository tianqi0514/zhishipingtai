from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from packages.semantica_adapter.file_safety import (
    UnsafeFileError,
    ZipLimits,
    safe_extract_zip,
    validate_file_identity,
)


class FileSafetyTest(unittest.TestCase):
    def test_rejects_magic_extension_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "fake.pdf"
            path.write_bytes(b"plain text")
            with self.assertRaisesRegex(UnsafeFileError, "有效的 .pdf"):
                validate_file_identity(path, "application/pdf")

    def test_zip_path_traversal_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.txt", "escape")
            with self.assertRaisesRegex(UnsafeFileError, "不安全路径"):
                safe_extract_zip(archive, root / "out")
            self.assertFalse((root / "escape.txt").exists())

    def test_zip_file_count_and_ratio_are_limited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = root / "many.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("a.txt", "a" * 2000)
                bundle.writestr("b.txt", "b")
            with self.assertRaisesRegex(UnsafeFileError, "文件数量"):
                safe_extract_zip(archive, root / "count", limits=ZipLimits(max_files=1))
            with self.assertRaisesRegex(UnsafeFileError, "压缩比"):
                safe_extract_zip(archive, root / "ratio", limits=ZipLimits(max_ratio=2))


if __name__ == "__main__":
    unittest.main()

