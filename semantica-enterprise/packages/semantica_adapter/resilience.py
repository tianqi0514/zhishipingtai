from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx


T = TypeVar("T")


@dataclass(frozen=True)
class ExternalFailure:
    """A small, secret-safe classification for external service failures."""

    category: str
    retryable: bool
    error_type: str
    message: str


_TIMEOUT_NAMES = {
    "APITimeoutError",
    "ConnectTimeout",
    "ReadTimeout",
    "TimeoutError",
    "TimeoutException",
}
_CONNECTION_NAMES = {
    "APIConnectionError",
    "ConnectError",
    "ConnectionError",
    "ConnectionResetError",
    "RemoteProtocolError",
    "ResponseHandlingException",
}
_RATE_LIMIT_NAMES = {"RateLimitError"}


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 10:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def classify_external_failure(
    exc: BaseException,
    *,
    secrets: list[str] | tuple[str, ...] = (),
    message_limit: int = 500,
) -> ExternalFailure:
    """Classify a model/search transport error without leaking credentials.

    Several SDKs wrap ``httpx`` errors in their own exception classes.  The
    complete exception chain and a deliberately small message vocabulary are
    inspected so the Worker can report whether a failed batch is retryable
    without depending on one SDK's private exception hierarchy.
    """

    chain = _exception_chain(exc)
    names = {type(item).__name__ for item in chain}
    messages = " | ".join(str(item) for item in chain if str(item))
    lowered = messages.casefold()

    if (
        names & _TIMEOUT_NAMES
        or any(isinstance(item, (TimeoutError, httpx.TimeoutException)) for item in chain)
        or "timed out" in lowered
        or "timeout" in lowered
    ):
        category, retryable = "timeout", True
    elif names & _RATE_LIMIT_NAMES or "rate limit" in lowered or "too many requests" in lowered:
        category, retryable = "rate_limited", True
    elif (
        names & _CONNECTION_NAMES
        or any(isinstance(item, httpx.TransportError) for item in chain)
        or any(
            marker in lowered
            for marker in (
                "connection reset",
                "connection refused",
                "connection error",
                "temporarily unavailable",
                "server disconnected",
            )
        )
    ):
        category, retryable = "connection", True
    elif any(marker in lowered for marker in ("invalid json", "failed to parse json", "validation")):
        category, retryable = "invalid_response", False
    else:
        category, retryable = "unknown", False

    safe_message = str(exc)
    for secret in secrets:
        if secret:
            safe_message = safe_message.replace(secret, "***")
    return ExternalFailure(
        category=category,
        retryable=retryable,
        error_type=type(exc).__name__,
        message=safe_message[: max(1, int(message_limit))],
    )


def retry_transient_call(
    operation: Callable[[], T],
    *,
    max_attempts: int = 3,
    initial_delay_seconds: float = 0.25,
    max_delay_seconds: float = 2.0,
    sleeper: Callable[[float], Any] = time.sleep,
) -> T:
    """Retry only transient external failures with bounded exponential delay.

    Callers must use this helper only for idempotent operations.  Permanent
    validation/authentication failures are returned immediately so retries do
    not hide configuration errors or multiply model cost.
    """

    attempts = max(1, min(int(max_attempts), 5))
    delay = max(0.0, min(float(initial_delay_seconds), 5.0))
    delay_cap = max(delay, min(float(max_delay_seconds), 10.0))
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            failure = classify_external_failure(exc)
            if not failure.retryable or attempt >= attempts:
                raise
            sleeper(min(delay * (2 ** (attempt - 1)), delay_cap))
    raise AssertionError("unreachable")
