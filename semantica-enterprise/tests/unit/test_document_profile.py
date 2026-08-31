from __future__ import annotations

import unittest
from types import SimpleNamespace

from packages.semantica_adapter.profile import analyze_profile_with_model, build_deterministic_profile


class DocumentProfileTest(unittest.TestCase):
    def test_deterministic_quality_detects_duplicates_and_missing_transcript(self) -> None:
        elements = [
            SimpleNamespace(
                text="传神智库面向集团知识治理。",
                page_number=1,
                structural_path="pages/1",
                element_type="paragraph",
                element_metadata={},
            ),
            SimpleNamespace(
                text="传神智库面向集团知识治理。",
                page_number=3,
                structural_path="pages/3",
                element_type="paragraph",
                element_metadata={},
            ),
            SimpleNamespace(
                text='{"duration": 12}',
                page_number=None,
                structural_path="video/metadata",
                element_type="video",
                element_metadata={"transcription_status": "not_configured"},
            ),
        ]
        profile = build_deterministic_profile(elements)
        self.assertEqual(profile.language, "zh-CN")
        self.assertGreater(profile.duplicate_ratio, 0)
        self.assertIn(2, profile.metrics["missing_pages"])
        self.assertTrue(any("转写" in issue for issue in profile.quality_issues))
        self.assertLess(profile.quality_score, 100)

    def test_model_profile_is_normalized_and_bounded(self) -> None:
        result = analyze_profile_with_model(
            "产品定位事实",
            model="contract-model",
            api_key="not-used",
            base_url=None,
            tag_count=2,
            generator=lambda _prompt: {
                "summary": "企业知识中枢",
                "classification": "产品资料",
                "document_type": "产品手册",
                "tags": ["知识库", "AI", "多余"],
                "keywords": ["治理", "检索", "多余"],
                "main_objects": ["NexusOne"],
                "time_range": {"start": "2026", "end": "2026"},
                "quality_issues": [],
            },
        )
        self.assertEqual(result["classification"], "产品资料")
        self.assertEqual(result["tags"], ["知识库", "AI"])
        self.assertEqual(result["main_objects"], ["NexusOne"])


if __name__ == "__main__":
    unittest.main()

