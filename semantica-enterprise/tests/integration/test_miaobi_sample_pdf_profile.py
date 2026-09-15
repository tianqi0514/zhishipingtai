"""Read the supplied customer sample for real layout/structure extraction."""

import pdfplumber
import pytest
from pathlib import Path

from packages.platform.writing_sample_profile import (
    extract_sample_profile, sample_main_body_lengths, validate_sample_profile,
)


SAMPLE = Path("/Users/tianqi/Desktop/积石山县6.2级地震_本体驱动应急智能推演系统_完整升级版/样稿.pdf")


def test_customer_pdf_yields_formal_plan_configuration_without_business_formulas() -> None:
    if not SAMPLE.exists():
        pytest.skip("客户样稿未挂载；此合约需读取真实 PDF")
    with pdfplumber.open(SAMPLE) as pdf:
        text = "\n".join((page.extract_text(layout=True) or "") for page in pdf.pages)
        assert len(pdf.pages) == 19
    profile = extract_sample_profile(text, version_id="sample-version", source_sha256="sample-sha")
    assert [row["title"] for row in profile["chapters"]] == [
        "总则", "组织体系", "运行机制", "应急保障", "其他地震事件应急", "监督管理", "附则",
    ]
    assert profile["style"]["notice_page_detected"] is True
    assert profile["style"]["reference_characters"] > 10_000
    assert 9_000 <= profile["style"]["reference_body_characters"] <= 12_000
    assert profile["style"]["reference_section_characters"]["sample-section-3"] > 4_000
    assert profile["style"]["body_boundary_method"] == "numbered-chapter-to-first-attachment-v1"
    assert profile["indicator_candidates"] == []
    assert profile["formula_candidates"] == []
    assert len(profile["attachments"]) >= 2
    normalized_chapters = validate_sample_profile(profile, source_version_id="sample-version", source_sha256="sample-sha")
    assert len(normalized_chapters) == 7
    assert sample_main_body_lengths(text, normalized_chapters)["reference_body_characters"] == profile["style"]["reference_body_characters"]
    with pytest.raises(ValueError, match="样稿版本"):
        validate_sample_profile(profile, source_version_id="new-version", source_sha256="sample-sha")
