from __future__ import annotations

from typing import Any


def apply_model_transport_options(
    provider: Any,
    *,
    timeout: float = 60,
    max_retries: int = 2,
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
    return provider
