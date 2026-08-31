from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from packages.semantica_adapter.capability import build_capability_report
from packages.semantica_adapter.parse import _pdf_text_needs_fallback, parse_document


class SemanticaAdapterContractTest(unittest.TestCase):
    def test_m4_runtime_capabilities_are_available(self) -> None:
        report = build_capability_report()
        required = {
            item.key: item.available
            for item in report.items
            if item.required_for.startswith("M4") or item.key == "semantica"
        }

        self.assertTrue(report.ready_for_m4, required)
        self.assertEqual(report.semantica_version, "0.6.6")

    def test_text_parse_has_stable_element_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "sample.txt"
            path.write_text("国联集团知识平台 M4", encoding="utf-8")

            first, first_summary = parse_document(path, version_id="version-1", policy={})
            second, second_summary = parse_document(path, version_id="version-1", policy={})

        self.assertEqual(first_summary, second_summary)
        self.assertEqual(first[0].element_id, second[0].element_id)
        self.assertEqual(first[0].text, "国联集团知识平台 M4")
        self.assertEqual(first_summary["parser"], "text")

    def test_control_characters_are_removed_before_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "embedded-font.txt"
            path.write_bytes(b"NexusOne\x00\x01 brochure\n2026")

            elements, _ = parse_document(path, version_id="version-control", policy={})

        self.assertEqual(elements[0].text, "NexusOne brochure\n2026")

    def test_corrupt_pdf_text_requests_quality_fallback(self) -> None:
        self.assertTrue(_pdf_text_needs_fallback({"full_text": "A\x00\x01B"}))
        self.assertFalse(_pdf_text_needs_fallback({"full_text": "正常的产品手册正文内容"}))

    def test_semantica_image_ocr_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "ocr.png"
            image = Image.new("RGB", (1000, 180), "white")
            draw = ImageDraw.Draw(image)
            font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
            font = ImageFont.truetype(str(font_path), 58) if font_path.exists() else ImageFont.load_default()
            draw.text((30, 45), "SEMANTICA OCR 100", fill="black", font=font)
            image.save(path)

            elements, summary = parse_document(
                path,
                version_id="image-version-1",
                policy={"enable_ocr": True, "ocr_language": "eng"},
            )

        normalized = elements[0].text.upper()
        self.assertEqual(summary["parser"], "image")
        self.assertIn("SEMANTICA", normalized)
        self.assertIn("100", normalized)


if __name__ == "__main__":
    unittest.main()
