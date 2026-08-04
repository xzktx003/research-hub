"""Unit tests for the reusable exponential-backoff retry helper.

Verifies the three core contracts that adapters rely on:

  * transient (retryable) failures are retried the configured number of times;
  * deterministic failures are NOT retried and propagate immediately;
  * backoff delays grow exponentially (with jitter) between attempts.
"""

from __future__ import annotations

import httpx
import pytest

from research_hub.adapters.retry import (
    RetryConfig,
    compute_delay,
    default_is_retryable,
    retry_backoff,
    run_with_retry,
)


class _SequenceFailure(ConnectionError):
    """A transient, retryable failure raised a fixed number of times.

    Subclasses ``ConnectionError`` so it is classified as retryable by
    ``default_is_retryable`` (mirroring a flaky network call).
    """


def _flaky(fail_times: int, value: str = "ok"):
    """Return a callable that raises ``fail_times`` times then returns ``value``."""
    calls = {"n": 0}

    def impl():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise _SequenceFailure(f"flaky attempt {calls['n']}")
        return value

    return impl, calls


def test_retryable_error_is_classified_for_retry() -> None:
    request = httpx.Request("GET", "https://example.test")
    cases = [
        httpx.ConnectError("boom", request=request),
        httpx.ReadTimeout("boom", request=request),
        httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request)),
        httpx.HTTPStatusError("500", request=request, response=httpx.Response(500, request=request)),
        httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request)),
    ]
    for exc in cases:
        assert default_is_retryable(exc) is True


def test_deterministic_error_is_not_retried() -> None:
    request = httpx.Request("GET", "https://example.test")
    cases = [
        httpx.HTTPStatusError("400", request=request, response=httpx.Response(400, request=request)),
        httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request)),
        httpx.HTTPStatusError("422", request=request, response=httpx.Response(422, request=request)),
        ValueError("bad payload"),
        KeyError("missing"),
    ]
    for exc in cases:
        assert default_is_retryable(exc) is False


def test_retryable_failure_is_retried_until_success(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    impl, calls = _flaky(fail_times=2)
    result = run_with_retry(
        impl,
        max_attempts=5,
        base_delay=1.0,
        max_delay=100.0,
        jitter=0.0,
    )

    assert result == "ok"
    assert calls["n"] == 3  # first success on the third attempt


def test_retryable_failure_raises_after_max_attempts(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    impl, calls = _flaky(fail_times=100)
    with pytest.raises(_SequenceFailure):
        run_with_retry(
            impl,
            max_attempts=3,
            base_delay=1.0,
            jitter=0.0,
        )

    assert calls["n"] == 3  # exhausted, no further attempts
    assert len(sleeps) == 2  # one backoff between each pair of attempts


def test_deterministic_error_is_not_retried(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def impl():
        calls["n"] += 1
        raise ValueError("validation failed")

    with pytest.raises(ValueError):
        run_with_retry(impl, max_attempts=5, jitter=0.0)

    assert calls["n"] == 1  # only attempted once
    assert sleeps == []  # never slept


def test_http_status_error_429_is_retried() -> None:
    request = httpx.Request("GET", "https://example.test")
    httpx_429 = httpx.HTTPStatusError(
        "rate limited",
        request=request,
        response=httpx.Response(429, request=request),
    )

    def impl():
        raise httpx_429

    with pytest.raises(httpx.HTTPStatusError):
        run_with_retry(impl, max_attempts=3, jitter=0.0)


def test_http_status_error_400_is_not_retried() -> None:
    request = httpx.Request("GET", "https://example.test")
    httpx_400 = httpx.HTTPStatusError(
        "bad request",
        request=request,
        response=httpx.Response(400, request=request),
    )
    calls = {"n": 0}

    def impl():
        calls["n"] += 1
        raise httpx_400

    with pytest.raises(httpx.HTTPStatusError):
        run_with_retry(impl, max_attempts=3, jitter=0.0)
    assert calls["n"] == 1


def test_backoff_delays_grow_exponentially(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    impl = _flaky(fail_times=100)[0]
    with pytest.raises(_SequenceFailure):
        run_with_retry(impl, max_attempts=5, base_delay=1.0, max_delay=100.0, jitter=0.0)

    # Each retry doubles the previous delay: 1, 2, 4, 8.
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_backoff_is_capped_at_max_delay(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    impl = _flaky(fail_times=100)[0]
    with pytest.raises(_SequenceFailure):
        run_with_retry(impl, max_attempts=6, base_delay=1.0, max_delay=3.0, jitter=0.0)

    assert sleeps == [1.0, 2.0, 3.0, 3.0, 3.0]


def test_retry_after_header_overrides_backoff(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    request = httpx.Request("GET", "https://example.test")
    exc = httpx.HTTPStatusError(
        "rate limited",
        request=request,
        response=httpx.Response(429, headers={"Retry-After": "2.5"}, request=request),
    )

    def impl():
        raise exc

    with pytest.raises(httpx.HTTPStatusError):
        run_with_retry(impl, max_attempts=3, base_delay=1.0, jitter=0.0)

    assert sleeps == [2.5, 2.5]


def test_retry_backoff_decorator_retries_and_returns(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    impl, calls = _flaky(fail_times=1)

    @retry_backoff(max_attempts=4, base_delay=1.0, jitter=0.0)
    def decorated():
        return impl()

    assert decorated() == "ok"
    assert calls["n"] == 2


def test_compute_delay_with_jitter_is_within_bounds() -> None:
    # Deterministic bounds with jitter=0 first.
    assert compute_delay(0, base_delay=1.0, max_delay=100.0, jitter=0.0) == 1.0
    assert compute_delay(1, base_delay=1.0, max_delay=100.0, jitter=0.0) == 2.0
    # With jitter the raw exponential stays in [2.0, 2.0 + jitter).
    value = compute_delay(1, base_delay=1.0, max_delay=100.0, jitter=0.5)
    assert 2.0 <= value < 2.5


def test_retry_config_clamps_values() -> None:
    config = RetryConfig(max_attempts=0, base_delay=-1.0, max_delay=5.0, jitter=-0.2)
    assert config.max_attempts == 1
    assert config.base_delay == 0.0
    assert config.max_delay == 5.0
    assert config.jitter == 0.0


def test_none_retry_config_uses_defaults(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))

    impl = _flaky(fail_times=100)[0]
    with pytest.raises(_SequenceFailure):
        run_with_retry(impl, config=RetryConfig())

    # Defaults: max_attempts=3 -> 2 retries with base_delay=1.0, jitter>0.
    assert len(sleeps) == 2


def test_on_retry_callback_receives_error_and_delay(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("research_hub.adapters.retry.time.sleep", lambda s: sleeps.append(s))
    recorded: list[tuple[BaseException, int, float]] = []

    def on_retry(exc, attempt, delay):
        recorded.append((exc, attempt, delay))

    impl = _flaky(fail_times=100)[0]
    with pytest.raises(_SequenceFailure):
        run_with_retry(
            impl,
            max_attempts=3,
            base_delay=1.0,
            jitter=0.0,
            on_retry=on_retry,
        )

    assert len(recorded) == 2
    assert [attempt for _, attempt, _ in recorded] == [0, 1]
    assert [delay for _, _, delay in recorded] == [1.0, 2.0]
