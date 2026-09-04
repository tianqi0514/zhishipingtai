from types import SimpleNamespace
from unittest.mock import Mock

from packages.semantica_adapter.llm_transport import (
    apply_model_transport_options,
    model_request_extra_body,
)


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


def test_private_model_request_parameters_are_allowlisted_and_forwarded() -> None:
    completions = SimpleNamespace(create=Mock(return_value=object()))
    configured = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client = SimpleNamespace(with_options=Mock(return_value=configured))
    provider = SimpleNamespace(client=client)

    apply_model_transport_options(
        provider,
        request_parameters={
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": "must not be forwarded"}],
        },
    )
    provider.client.chat.completions.create(model="private-model", messages=[])

    completions.create.assert_called_once_with(
        model="private-model",
        messages=[],
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    assert model_request_extra_body({"model": "other", "enable_thinking": False}) == {
        "enable_thinking": False
    }
