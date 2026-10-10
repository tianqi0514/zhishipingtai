from scripts.verification.verify_writing_assets_report import changed_confirmed_inputs


def test_report_verifier_detects_manual_confirmed_changes_without_resetting_inputs():
    facts = [
        {"fact_key": "unit_cost", "value": {"number": 5500}, "verification_status": "verified"},
        {"fact_key": "area", "value": {"number": 20000}, "verification_status": "verified"},
        {"fact_key": "unconfirmed", "value": {"number": 10}, "verification_status": "unverified"},
    ]
    definitions = [("unit_cost", "单价", {"number": 5000}, "元/平方米"),
                   ("area", "面积", {"number": 20000}, "平方米"),
                   ("unconfirmed", "待确认", {"number": 20}, "")]
    assert changed_confirmed_inputs(facts, definitions) == ["unit_cost"]
    assert facts[0]["value"]["number"] == 5500
