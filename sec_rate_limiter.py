"""SEC project helper: process-local rate limiter for SEC fair-access.

This module provides a simple token-bucket RateLimiter and a module-level
instance accessible via `get_rate_limiter()`.

Usage:
    from sec_rate_limiter import get_rate_limiter
    limiter = get_rate_limiter()
    limiter.acquire()

The default rate is controlled by the SEC_MAX_RPS environment variable
(default: 10 requests/second).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

DEFAULT_MAX_RPS: int = int(os.environ.get("SEC_MAX_RPS", "10"))


class RateLimiter:
    """Simple token-bucket rate limiter (synchronous, thread-safe).

    Allows `max_calls` tokens per `period` seconds. Call `acquire()` to
    block until a token is available and consume one.
    """

    def __init__(self, max_calls: int, period: float = 1.0) -> None:
        self._max_calls = float(max_calls)
        self._period = float(period)
        self._lock = threading.Lock()
        self._tokens = float(max_calls)
        # monotonic timestamp of last refill
        self._last = time.monotonic()

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                if elapsed > 0:
                    # refill proportionally to elapsed time
                    refill = elapsed * (self._max_calls / self._period)
                    if refill > 0:
                        self._tokens = min(self._max_calls, self._tokens + refill)
                        self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # compute sleep time until one token becomes available
                need = 1.0 - self._tokens
                sleep_time = need * (self._period / self._max_calls)
            # sleep outside the lock
            time.sleep(sleep_time)


# module-level limiter instance
_GLOBAL_LIMITER: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Return a process-global RateLimiter. Instantiates on first access.

    The limiter's rate is read from the SEC_MAX_RPS environment variable
    at import time (default 10 req/sec). If you need to change the rate
    at runtime, set SEC_MAX_RPS before importing this module.
    """
    global _GLOBAL_LIMITER
    if _GLOBAL_LIMITER is None:
        _GLOBAL_LIMITER = RateLimiter(DEFAULT_MAX_RPS, period=1.0)
    return _GLOBAL_LIMITER
