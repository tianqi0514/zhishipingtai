"""Offline packaging contract; all material, state and export bytes are synthetic."""

import copy
import hashlib
import importlib.util
import json
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/miaobi/package_qinglan_demo.py"
_SPEC = importlib.util.spec_from_file_location("qinglan_demo_package", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
package = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(package)


def word_bytes() -> bytes:
    """Small valid ZIP envelope, not a rendered or platform-generated report."""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


class TestQinglanDemoPackage(unittest.TestCase):
    def setUp(self):
        # This path is never created, read or written. The source script is the
        # only real file imported; no current demo materials or exports are used.
        self.root = Path("/qinglan-package-fixture")
        self.source = self.root / "demo/miaobi/qinglan-live-20260914"
        self.output = self.root / ".demo-build/qinglan-live-20260914"
        self.word = word_bytes()
        self.pdf = b"%PDF-1.7\nsynthetic signature fixture\n%%EOF"
        self.payloads = {
            name: self.pdf if fmt == "pdf" else self.word
            for fmt, name in package.EXPORT_NAMES.items()
        }
        self.metadata = [
            {
                "format": fmt,
                "path": name,
                "bytes": len(self.payloads[name]),
                "sha256": hashlib.sha256(self.payloads[name]).hexdigest(),
                "document_version_id": "isolated-fixture-version",
            }
            for fmt, name in package.EXPORT_NAMES.items()
        ]
        self.state = {"finalized": {"status": "completed", "quality_report": {"ok": True}}}
        self.report = {
            "current_version_id": "isolated-fixture-version",
            "current_version": {"id": "isolated-fixture-version"},
        }
        self.files = {
            self.source / "01-上传材料" / name: (
                self.word if name.endswith(".docx") else self.pdf if name.endswith(".pdf") else b"demo"
            )
            for name in package.MATERIAL_NAMES
        }
        # Distinct material hashes expose filename/hash swaps, including between DOCX files.
        for name in package.MATERIAL_NAMES:
            self.files[self.source / "01-上传材料" / name] += name.encode()
        self.state["uploaded"] = [
            {
                "filename": name,
                "version": {
                    "sha256": hashlib.sha256(self.files[self.source / "01-上传材料" / name]).hexdigest()
                },
            }
            for name in package.MATERIAL_NAMES
        ]
        self.files[self.source / "02-变更参考/05-人员到位更新.md"] = "仅供演示，人员到位更新。".encode()
        self.files[self.source / "source_truth.json"] = b'{"synthetic": true}'
        self.guide = "\n".join(
            [
                "仅供演示，不代表真实灾情，不构成调度命令。",
                "`demo/miaobi/qinglan-live-20260914/01-上传材料`",
                *[
                    f"[{name}](</author/repository/demo/miaobi/qinglan-live-20260914/01-上传材料/{name}>)"
                    for name in package.MATERIAL_NAMES
                ],
                "[source_truth.json](/author/repository/demo/miaobi/qinglan-live-20260914/source_truth.json)",
                "[测试记录](/author/repository/docs/miaobi/MIAOBI_QINGLAN_LIVE_TEST_REPORT.md)",
                "[界面源码](/author/repository/apps/miaobi-web/src/App.tsx)",
            ]
        ).encode()
        self.files[self.root / "docs/miaobi/MIAOBI_QINGLAN_LIVE_DEMO.md"] = self.guide
        report_link = (
            "[报告](/author/repository/.demo-build/qinglan-live-20260914/"
            + package.EXPORT_NAMES["docx"]
            + ")"
        )
        self.files[self.root / "docs/miaobi/MIAOBI_QINGLAN_LIVE_TEST_REPORT.md"] = report_link.encode()
        # Decoys ensure the packer never enumerates unrelated material or state.
        self.files[self.source / "qa/page-1.png"] = b"exclude QA"
        self.files[self.root / "deploy/secrets/demo-secret"] = b"exclude Secret"
        self.files[self.root / "demo/other-project/original.docx"] = b"exclude unrelated originals"
        self.read_paths = []

    def build(self, *, exports=None, run_state=None, payloads=None, extra_materials=(), report=None):
        def read(base, relative):
            path = base / relative
            self.read_paths.append(path)
            if base == self.output:
                if relative == "state.json":
                    return json.dumps(self.state if run_state is None else run_state).encode()
                if relative == "exports.json":
                    return json.dumps(self.metadata if exports is None else exports).encode()
                if relative == "report_document.json":
                    return json.dumps(self.report if report is None else report).encode()
                return (self.payloads if payloads is None else payloads)[relative]
            return self.files[path]

        material_paths = [
            self.source / "01-上传材料" / name
            for name in (*package.MATERIAL_NAMES, *extra_materials)
        ]
        with (
            patch.object(package, "checked_read", side_effect=read),
            patch.object(Path, "iterdir", return_value=iter(material_paths)),
        ):
            return package.build_entries(self.root)

    def test_packaged_member_allowlist(self):
        expected = {"01-上传材料/" + name for name in package.MATERIAL_NAMES}
        expected |= {"03-已生成报告/" + name for name in package.EXPORT_NAMES.values()}
        expected |= {
            "02-变更参考/05-人员到位更新.md",
            "测试核对-不要上传/source_truth.json",
            "开始这里-演示脚本.md",
            "测试记录.md",
            "文件校验.json",
        }
        self.assertEqual(set(self.build()), expected)
        self.assertEqual(len(expected), 12)
        self.assertNotIn(self.source / "qa/page-1.png", self.read_paths)
        self.assertNotIn(self.root / "deploy/secrets/demo-secret", self.read_paths)
        self.assertNotIn(self.root / "demo/other-project/original.docx", self.read_paths)

    def test_all_links_portable(self):
        entries = self.build()
        for name in ("开始这里-演示脚本.md", "测试记录.md"):
            text = entries[name].decode()
            self.assertNotIn("/author/", text)
            for match in package.MARKDOWN_LINK.finditer(text):
                target = match.group(2).strip("<>").partition("#")[0]
                if target and not target.startswith(("http://", "https://", "mailto:")):
                    self.assertIn(target, entries)
        guide = entries["开始这里-演示脚本.md"].decode()
        self.assertIn("](测试核对-不要上传/source_truth.json)", guide)
        self.assertIn("包外资料，需在原仓库或测试服务器查看", guide)
        self.assertIn("`01-上传材料`", guide)

    def test_all_delivered_members_are_hashed(self):
        entries = self.build()
        manifest = json.loads(entries["文件校验.json"])
        self.assertEqual(set(manifest), set(entries) - {"文件校验.json"})
        for name, checksum in manifest.items():
            self.assertEqual(checksum, hashlib.sha256(entries[name]).hexdigest())

    def test_source_materials_stay_byte_identical(self):
        entries = self.build()
        for name in package.MATERIAL_NAMES:
            self.assertEqual(
                entries["01-上传材料/" + name], self.files[self.source / "01-上传材料" / name]
            )
        self.assertEqual(self.files[self.root / "docs/miaobi/MIAOBI_QINGLAN_LIVE_DEMO.md"], self.guide)

    def test_zip_roundtrip(self):
        entries = self.build()
        buffer = BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        with ZipFile(buffer) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), set(entries))
            for name, data in entries.items():
                self.assertEqual(archive.read(name), data)

    def test_unfinished_quality_rejected(self):
        for finalized in (
            {"status": "running"},
            {"status": "completed"},
            {"status": "completed", "quality_report": {"ok": False}},
        ):
            with self.subTest(finalized=finalized), self.assertRaises(ValueError):
                self.build(run_state={"finalized": finalized})

    def test_partial_exports_rejected(self):
        for exports in ([], self.metadata[:1], self.metadata[:2]):
            with self.subTest(count=len(exports)), self.assertRaises(ValueError):
                self.build(exports=exports)

    def test_duplicate_format_rejected(self):
        with self.assertRaises(ValueError):
            self.build(exports=[self.metadata[0], self.metadata[0], self.metadata[2]])

    def test_changed_material_rejected(self):
        for name in package.MATERIAL_NAMES:
            path = self.source / "01-上传材料" / name
            original = self.files[path]
            self.files[path] += b"changed"
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "上传记录不匹配"):
                self.build()
            self.files[path] = original

    def test_uploaded_hashes_are_matched_by_filename(self):
        state = copy.deepcopy(self.state)
        first, third = state["uploaded"][0], state["uploaded"][2]
        first["version"], third["version"] = third["version"], first["version"]
        with self.assertRaisesRegex(ValueError, "上传记录不匹配"):
            self.build(run_state=state)

    def test_upload_order_is_irrelevant(self):
        state = copy.deepcopy(self.state)
        state["uploaded"].reverse()
        self.assertEqual(self.build(run_state=state), self.build())

    def test_incomplete_or_duplicate_upload_records_rejected(self):
        records = self.state["uploaded"]
        for uploaded in (None, [], records[:3], [records[0], records[0], *records[2:]]):
            state = copy.deepcopy(self.state)
            state["uploaded"] = uploaded
            with self.subTest(uploaded=uploaded), self.assertRaises(ValueError):
                self.build(run_state=state)

    def test_unknown_upload_filename_rejected(self):
        state = copy.deepcopy(self.state)
        state["uploaded"][0]["filename"] = "other-material.docx"
        with self.assertRaises(ValueError):
            self.build(run_state=state)

    def test_missing_or_invalid_uploaded_hash_rejected(self):
        for version in (None, {}, {"sha256": None}, {"sha256": "wrong"}, "invalid"):
            state = copy.deepcopy(self.state)
            state["uploaded"][0]["version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.build(run_state=state)

    def test_same_old_version_exports_rejected(self):
        exports = copy.deepcopy(self.metadata)
        for item in exports:
            item["document_version_id"] = "same-but-old-version"
        with self.assertRaisesRegex(ValueError, "匹配文稿快照"):
            self.build(exports=exports)

    def test_missing_or_inconsistent_current_report_version_rejected(self):
        for report in (
            [], {}, {"current_version_id": ""},
            {"current_version_id": "isolated-fixture-version", "current_version": None},
            {"current_version_id": "isolated-fixture-version", "current_version": {}},
            {"current_version_id": "isolated-fixture-version", "current_version": {"id": "other"}},
        ):
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, "文稿快照"):
                self.build(report=report)

    def test_mixed_versions_rejected(self):
        exports = copy.deepcopy(self.metadata)
        exports[0]["document_version_id"] = "different"
        with self.assertRaises(ValueError):
            self.build(exports=exports)

    def test_missing_version_rejected(self):
        exports = copy.deepcopy(self.metadata)
        for item in exports:
            item.pop("document_version_id")
        with self.assertRaises(ValueError):
            self.build(exports=exports)

    def test_manifest_path_injection_rejected(self):
        for path in ("../state.json", "/tmp/Secret", "qa/page-1.png", "../other-project/original.docx"):
            exports = copy.deepcopy(self.metadata)
            exports[0]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.build(exports=exports)

    def test_bad_hash_rejected(self):
        exports = copy.deepcopy(self.metadata)
        exports[0]["sha256"] = "bad"
        with self.assertRaises(ValueError):
            self.build(exports=exports)

    def test_bad_size_rejected(self):
        exports = copy.deepcopy(self.metadata)
        exports[0]["bytes"] += 1
        with self.assertRaises(ValueError):
            self.build(exports=exports)

    def test_bad_pdf_signature_rejected(self):
        payloads = dict(self.payloads)
        payloads[package.EXPORT_NAMES["pdf"]] = b"not a PDF"
        exports = copy.deepcopy(self.metadata)
        item = next(item for item in exports if item["format"] == "pdf")
        item.update(
            bytes=len(payloads[item["path"]]), sha256=hashlib.sha256(payloads[item["path"]]).hexdigest()
        )
        with self.assertRaises(ValueError):
            self.build(exports=exports, payloads=payloads)

    def test_incomplete_word_rejected(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("README.txt", "not a document")
        payloads = dict(self.payloads)
        payloads[package.EXPORT_NAMES["docx"]] = buffer.getvalue()
        exports = copy.deepcopy(self.metadata)
        item = next(item for item in exports if item["format"] == "docx")
        item.update(
            bytes=len(payloads[item["path"]]), sha256=hashlib.sha256(payloads[item["path"]]).hexdigest()
        )
        with self.assertRaises(ValueError):
            self.build(exports=exports, payloads=payloads)

    def test_direct_outside_paths_rejected(self):
        for path in ("../Secret", "/tmp/Secret", "qa/../../Secret", "qa\\Secret"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                package.checked_read(self.source, path)

    def test_symlinks_rejected(self):
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(ValueError):
            package.checked_read(self.source, "source_truth.json")

    def test_extra_initial_file_rejected(self):
        with self.assertRaises(ValueError):
            self.build(extra_materials=("Secret.md",))

    def test_rewrite_preserves_web_anchor(self):
        text = (
            "[truth](</different/host/demo/miaobi/qinglan-live-20260914/source_truth.json>) "
            "[web](https://example.test/a#b) [same](#b) [source](/work/apps/file.py)"
        )
        rewritten = package.portable_markdown(
            text,
            {"demo/miaobi/qinglan-live-20260914/source_truth.json": "测试核对-不要上传/source_truth.json"},
        )
        self.assertIn("[truth](测试核对-不要上传/source_truth.json)", rewritten)
        self.assertIn("[web](https://example.test/a#b)", rewritten)
        self.assertIn("[same](#b)", rewritten)
        self.assertNotIn("/work/", rewritten)

    def test_base_ancestor_symlink_rejected(self):
        with (
            patch.object(Path, "is_symlink", lambda path: path == self.root / "demo"),
            self.assertRaises(ValueError),
        ):
            package.checked_read(self.source, "source_truth.json")

    def test_existing_output_zip_symlink_rejected_without_write(self):
        with (
            patch.object(package, "build_entries", return_value={"test": b"test"}),
            patch.object(Path, "is_symlink", return_value=True),
            patch.object(Path, "write_bytes") as writer,
        ):
            with self.assertRaises(ValueError):
                package.main()
            writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
