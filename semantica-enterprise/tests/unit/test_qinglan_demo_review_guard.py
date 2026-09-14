"""The live demo runner must not silently approve a user's decision gates."""
import pytest

from scripts.miaobi.acceptance_qinglan_live import require_confirmed_export_gates


def test_confirmed_decisions_allow_export_check():
    assert require_confirmed_export_gates([{"status": "confirmed"}]) is None


def test_no_local_gates_defers_to_server_publish_validation():
    assert require_confirmed_export_gates([]) is None


@pytest.mark.parametrize("status", ["pending", "rejected", "stale", None])
def test_unconfirmed_gate_blocks_without_changing_it(status):
    gate = {"status": status, "id": "private-internal-id"}
    with pytest.raises(RuntimeError, match="不会代替人工确认") as error:
        require_confirmed_export_gates([{"status": "confirmed"}, gate])
    assert gate == {"status": status, "id": "private-internal-id"}
    assert "private-internal-id" not in str(error.value)
