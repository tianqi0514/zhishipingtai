#!/usr/bin/env python3
"""Exercise real Semantica/adapters and Docker system dependencies on fixtures."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.semantica_adapter.parse import parse_document
from tests.fixtures.generate_multimodal import generate


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="chuanshen-multimodal-") as temporary:
        fixtures = generate(Path(temporary))
        results = {}
        for filename, path in sorted(fixtures.items()):
            elements, summary = parse_document(
                path,
                version_id=f"fixture-{filename}",
                policy={
                    "parser_type": "auto",
                    "enable_ocr": True,
                    "ocr_language": "eng",
                    "extract_tables": True,
                    "parse_email_attachments": True,
                    "zip_max_files": 20,
                    "zip_max_total_bytes": 20 * 1024 * 1024,
                },
            )
            assert elements, filename
            assert summary["element_count"] == len(elements), filename
            if filename in {"fact.png", "fact.jpg", "scanned.pdf"}:
                joined = "\n".join(item.text for item in elements)
                assert "NexusOne" in joined, (filename, joined[:500])
            if filename == "fact.eml":
                assert any("attachment_parent" in item.metadata for item in elements), summary
            if filename in {"fact.wav", "fact.mp3", "fact.mp4"}:
                assert summary["transcription_status"] == "not_configured", summary
            if filename in {"fact.doc", "fact.ppt", "fact.xls"}:
                assert summary["parser"].startswith("libreoffice+"), summary
            results[filename] = {
                "parser": summary["parser"],
                "elements": len(elements),
                "types": summary["types"],
                "transcription_status": summary.get("transcription_status"),
            }
        print(json.dumps({"formats": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
