"""Reusable exponential-backoff retry helpers for external API calls.

Standard-library only (no new runtime dependencies). Provides:

  * ``RetryConfig`` — knobs for attempt count / delay / jitter.
  * ``compute_delay`` — exponential backoff delay (optionally honoring
    ``Retry-After`` headers).
  * ``default_is_retryable`` — classify network/timeout/HTTP 429/5xx errors as
    transient; anything deterministic (validation failures, 4xx parameter
    errors) is not retried.
  * ``run_with_retry`` — retry an arbitrary callable.
  * ``retry_backoff`` — decorator form of ``run_with_retry``.

Callers pass their own timeout logic (e.g. an ``httpx.Client(timeout=...)``)
unchanged; this layer only governs *whether* and *how long* to wait between
attempts.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

import httpx

T = TypeVar("T")


@dataclass(frozen=True)
class RetryConfig:
    """Backoff configuration.

    ``max_attempts`` is the total number of attempts (including the first).
    ``base_delay`` is the initial sleep; each successive retry doubles it up
    to ``max_delay``. ``jitter`` adds a random ``[0, jitter)`` term so
    colocated callers do not retry in lockstep.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_attempts", max(1, int(self.max_attempts)))
        object.__setattr__(self, "base_delay", max(0.0, float(self.base_delay)))
        object.__setattr__(self, "max_delay", max(self.base_delay, float(self.max_delay)))
        object.__setattr__(self, "jitter", max(0.0, float(self.jitter)))


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def compute_delay(
    attempt: int,
    *,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    retry_after: float | None = None,
) -> float:
    """Return the sleep length (seconds) before retry ``attempt`` (0-based)."""
    if retry_after is not None:
        return min(max(0.0, retry_after), max_delay)
    exponential = min(max_delay, base_delay * (2 ** max(0, attempt)))
    if jitter > 0:
        exponential = exponential + random.uniform(0.0, jitter)
    return min(exponential, max_delay)


def default_is_retryable(exc: BaseException) -> bool:
    """True for transient network/timeout/HTTP 429/5xx failures, else False."""
    if isinstance(exc, (httpx.RequestError, httpx.TransportError, httpx.TimeoutException)):
        return True
    status = _http_status(exc)
    if status is not None:
        return status == 429 or status >= 500
    if isinstance(exc, (OSError, ConnectionError, TimeoutError)):
        return True
    return False


def run_with_retry(
    fn: Callable[[], T],
    *,
    config: RetryConfig | None = None,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    is_retryable: Callable[[BaseException], bool] = default_is_retryable,
    sleep: Callable[[float], Any] | None = None,
    on_retry: Callable[[BaseException, int, float], Any] | None = None,
) -> T:
    """Call ``fn()``, retrying transient failures with exponential backoff.

    Deterministic errors (where ``is_retryable`` returns False) propagate
    immediately. When attempts are exhausted the last exception is re-raised.

    ``sleep`` defaults to ``time.sleep`` and is resolved at call time (rather
    than bound at definition time) so tests can monkeypatch
    ``research_hub.adapters.retry.time.sleep``.
    """
    if config is not None:
        max_attempts = config.max_attempts
        base_delay = config.base_delay
        max_delay = config.max_delay
        jitter = config.jitter
    do_sleep = sleep if sleep is not None else time.sleep
    attempts = max(1, int(max_attempts))
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - propagate below
            last_error = exc
            if attempt >= attempts - 1 or not is_retryable(exc):
                raise
            delay = compute_delay(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                retry_after=_retry_after_seconds(exc),
            )
            if on_retry is not None:
                on_retry(exc, attempt, delay)
            do_sleep(delay)
    raise AssertionError("unreachable") from last_error  # pragma: no cover


def retry_backoff(
    *,
    config: RetryConfig | None = None,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    is_retryable: Callable[[BaseException], bool] = default_is_retryable,
    sleep: Callable[[float], Any] | None = None,
    on_retry: Callable[[BaseException, int, float], Any] | None = None,
):
    """Decorator form of :func:`run_with_retry`."""
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return run_with_retry(
                lambda: fn(*args, **kwargs),
                config=config,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                is_retryable=is_retryable,
                sleep=sleep,
                on_retry=on_retry,
            )
        return wrapper
    return decorator
