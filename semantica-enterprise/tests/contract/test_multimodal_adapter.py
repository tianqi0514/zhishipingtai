from __future__ import annotations

import tempfile
import unittest
import wave
import zipfile
from email.message import EmailMessage
from pathlib import Path

from packages.semantica_adapter.parse import parse_document


class MultimodalAdapterContractTest(unittest.TestCase):
    def test_jsonl_code_and_safe_zip_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            jsonl = root / "facts.jsonl"
            jsonl.write_text('{"product":"NexusOne","rank":1}\n{"source":"manual","page":3}\n', encoding="utf-8")
            rows, row_summary = parse_document(jsonl, version_id="jsonl-v1", policy={})
            code = root / "sample.py"
            code.write_text("def priority(value):\n    return value + 1\n", encoding="utf-8")
            code_elements, code_summary = parse_document(code, version_id="code-v1", policy={})
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("notes/readme.md", "NexusOne 定位为企业知识中枢")
                bundle.writestr("facts/data.jsonl", jsonl.read_bytes())
            archive_elements, archive_summary = parse_document(archive, version_id="zip-v1", policy={})

        self.assertEqual(row_summary["parser"], "json")
        self.assertEqual(len(rows), 2)
        self.assertEqual(code_summary["parser"], "code")
        self.assertEqual(code_elements[0].element_type, "code")
        self.assertEqual(archive_summary["parser"], "safe-zip")
        self.assertTrue(any(item.structural_path.startswith("archive/notes/readme.md") for item in archive_elements))

    def test_email_body_and_attachment_are_recursively_parsed(self) -> None:
        message = EmailMessage()
        message["Subject"] = "传神智库产品资料"
        message["From"] = "product@example.test"
        message["To"] = "group@example.test"
        message.set_content("正文事实：产品面向集团知识治理。")
        message.add_attachment(
            "附件事实：支持全文、向量、图谱三路检索。".encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename="evidence.txt",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mail.eml"
            path.write_bytes(message.as_bytes())
            elements, summary = parse_document(path, version_id="email-v1", policy={})

        self.assertEqual(summary["parser"], "email")
        self.assertTrue(any(item.element_type == "email" and "集团知识治理" in item.text for item in elements))
        self.assertTrue(any("三路检索" in item.text and item.metadata.get("attachment_parent") for item in elements))

    def test_audio_metadata_degrades_without_asr_and_accepts_real_transcript_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "short.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 1600)
            metadata_only, degraded = parse_document(path, version_id="audio-v1", policy={})
            transcript, enriched = parse_document(
                path,
                version_id="audio-v2",
                policy={},
                media_transcriber=lambda _path, _type: {
                    "transcript": "NexusOne 支持知识治理",
                    "segments": [{"start": 0.0, "end": 0.1, "text": "NexusOne 支持知识治理"}],
                    "model": "test-asr-contract",
                    "transcription_status": "succeeded",
                },
            )

        self.assertEqual(degraded["transcription_status"], "not_configured")
        self.assertTrue(any(item.element_type == "audio" for item in metadata_only))
        self.assertEqual(enriched["transcription_status"], "succeeded")
        self.assertTrue(any(item.element_type == "transcript" and "知识治理" in item.text for item in transcript))

    def test_image_and_video_degrade_without_vision_and_accept_visual_descriptions(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "fact.png"
            Image.new("RGB", (24, 16), "white").save(path)
            _, degraded = parse_document(
                path,
                version_id="image-v1",
                policy={"enable_ocr": False},
            )
            enriched_elements, enriched = parse_document(
                path,
                version_id="image-v2",
                policy={"enable_ocr": False},
                visual_describer=lambda _path, _type: {
                    "vision_description": "图中展示 NexusOne 企业知识平台架构。",
                    "vision_status": "succeeded",
                    "vision_model": "vision-contract",
                    "keyframes": [{"time_start": 0.0, "time_end": 0.0}],
                },
            )

        self.assertEqual(degraded["vision_status"], "not_configured")
        self.assertEqual(enriched["vision_status"], "succeeded")
        self.assertTrue(any("企业知识平台架构" in item.text for item in enriched_elements))
        self.assertFalse(any("api_key" in str(item.metadata).lower() for item in enriched_elements))


if __name__ == "__main__":
    unittest.main()
