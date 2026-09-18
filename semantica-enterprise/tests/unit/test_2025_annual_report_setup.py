from scripts.miaobi.prepare_2025_annual_report import INPUTS, SECTIONS


def test_annual_report_inputs_are_2025_scoped_and_have_upload_guidance() -> None:
    assert len(INPUTS) >= 12
    for value in INPUTS.values():
        assert value["expected_period"].startswith("2025")
        assert value["source_guidance"]
        assert value["recommended_upload"]
        assert value["affects_sections"]


def test_every_required_input_points_to_a_real_report_section() -> None:
    section_keys = {key for key, _title, _instruction in SECTIONS}
    assert len(SECTIONS) == 8
    assert all(set(value["affects_sections"]) <= section_keys for value in INPUTS.values())


def test_2024_report_is_never_declared_as_a_2025_fact() -> None:
    serialized = str(INPUTS)
    assert "2024年度" not in serialized
    assert "expected_period': '2025" in serialized
