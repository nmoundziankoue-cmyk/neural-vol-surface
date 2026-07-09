"""Retry helper for yfinance calls. Yahoo's unofficial API doesn't fail
cleanly and consistently under rate-limiting: sometimes it raises
yfinance.exceptions.YFRateLimitError, sometimes an internal KeyError from
malformed metadata, sometimes it just prints a warning and returns an
empty/None result. We treat any exception *or* an invalid result as a
retryable failure rather than pattern-matching on a specific error type.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("daily_capture")

T = TypeVar("T")


class FetchFailure(Exception):
    """Raised when a wrapped call still fails/validates false after all retries."""


def with_retry(
    fn: Callable[[], T],
    *,
    label: str,
    validate: Callable[[T], bool] = lambda result: result is not None,
    delays: tuple[int, ...] = (30, 60, 120),
) -> T:
    """Call fn(), retrying on exception or failed validation.

    Total attempts = 1 + len(delays). With the default delays that's
    1 initial attempt + 3 retries (30s, 60s, 120s backoff).
    """
    last_error: Exception | None = None

    for attempt in range(1, len(delays) + 2):
        try:
            result = fn()
            if validate(result):
                if attempt > 1:
                    logger.info("[retry] %s succeeded on attempt %d", label, attempt)
                return result
            last_error = FetchFailure(f"{label}: empty/invalid result")
        except Exception as e:  # noqa: BLE001 - intentionally broad, see module docstring
            last_error = e

        is_last_attempt = attempt == len(delays) + 1
        if is_last_attempt:
            break

        delay = delays[attempt - 1]
        logger.warning(
            "[retry] %s failed (attempt %d/%d): %s -- retrying in %ds",
            label, attempt, len(delays) + 1, last_error, delay,
        )
        time.sleep(delay)

    logger.error("[retry] %s failed after %d attempts, giving up: %s", label, len(delays) + 1, last_error)
    raise FetchFailure(f"{label} failed after {len(delays) + 1} attempts: {last_error}") from last_error
