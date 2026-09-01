from types import SimpleNamespace
from unittest.mock import Mock

from packages.semantica_adapter.llm_transport import apply_model_transport_options


def test_model_transport_settings_are_applied_to_semantica_client() -> None:
    configured = object()
    client = SimpleNamespace(with_options=Mock(return_value=configured))
    provider = SimpleNamespace(client=client)

    assert apply_model_transport_options(provider, timeout=12, max_retries=3) is provider
    client.with_options.assert_called_once_with(timeout=12.0, max_retries=3)
    assert provider.client is configured


def test_model_transport_settings_are_safely_bounded() -> None:
    client = SimpleNamespace(with_options=Mock(return_value=object()))
    provider = SimpleNamespace(client=client)

    apply_model_transport_options(provider, timeout=9999, max_retries=99)

    client.with_options.assert_called_once_with(timeout=600.0, max_retries=10)
