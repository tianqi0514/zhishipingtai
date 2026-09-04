from __future__ import annotations

from typing import Any


_ALLOWED_REQUEST_PARAMETERS = {
    "chat_template_kwargs",
    "enable_thinking",
    "thinking_token_budget",
}


def model_request_extra_body(parameters: dict[str, Any] | None) -> dict[str, Any]:
    """Return the small, reviewed subset of OpenAI-compatible request extensions.

    Private vLLM deployments commonly expose model-specific switches outside
    the OpenAI schema.  Model configuration may opt into those switches, but
    it must not be able to replace messages, tools, the model name, or other
    platform-owned request fields.
    """
    if not isinstance(parameters, dict):
        return {}
    return {
        key: value
        for key, value in parameters.items()
        if key in _ALLOWED_REQUEST_PARAMETERS
        and isinstance(value, (dict, str, int, float, bool))
    }


class _CompletionsWithDefaults:
    def __init__(self, target: Any, extra_body: dict[str, Any]) -> None:
        self._target = target
        self._extra_body = extra_body

    def create(self, *args: Any, **kwargs: Any) -> Any:
        configured = dict(self._extra_body)
        supplied = kwargs.get("extra_body")
        if isinstance(supplied, dict):
            configured.update(supplied)
        kwargs["extra_body"] = configured
        return self._target.create(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class _ChatWithDefaults:
    def __init__(self, target: Any, extra_body: dict[str, Any]) -> None:
        self._target = target
        self.completions = _CompletionsWithDefaults(target.completions, extra_body)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


def apply_model_transport_options(
    provider: Any,
    *,
    timeout: float = 60,
    max_retries: int = 2,
    request_parameters: dict[str, Any] | None = None,
) -> Any:
    """Apply platform model transport settings to a Semantica provider.

    Semantica owns the provider and structured-output implementation.  This
    thin adapter only configures the underlying OpenAI-compatible client so a
    tenant's timeout/retry settings are honoured by Worker calls as well as by
    the model connection test.
    """
    timeout = max(0.1, min(float(timeout), 600.0))
    max_retries = max(0, min(int(max_retries), 10))
    client = getattr(provider, "client", None)
    if client is not None and hasattr(client, "with_options"):
        provider.client = client.with_options(timeout=timeout, max_retries=max_retries)
        extra_body = model_request_extra_body(request_parameters)
        if extra_body:
            # Keep the original OpenAI client instance so Semantica's optional
            # instructor integration can still recognise it by type.
            provider.client.chat = _ChatWithDefaults(provider.client.chat, extra_body)
    return provider
