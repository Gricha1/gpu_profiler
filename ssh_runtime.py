"""Process-wide SSH concurrency limit and lightweight diagnostics.

Every in-process SSH launcher must acquire this runtime.  The application is
intentionally single-worker; the counter is therefore an exact process-local
measurement rather than a distributed approximation.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter, deque
from contextlib import contextmanager
from typing import Any, Iterator

MAX_ACTIVE = max(1, int(os.getenv("GPU_MONITOR_SSH_MAX_ACTIVE", "3")))

_slots = threading.BoundedSemaphore(MAX_ACTIVE)
_lock = threading.Lock()
_active = 0
_peak = 0
_started: deque[float] = deque()
_attempts: Counter[str] = Counter()
_timeouts: Counter[str] = Counter()
_errors: Counter[str] = Counter()
_wait_total = 0.0
_wait_count = 0
_duration_total = 0.0
_duration_count = 0
_route_diagnostics = 0


def configure(max_active: int) -> None:
    """Configure the limit during application import, before slots are used."""
    global MAX_ACTIVE, _slots
    value = max(1, int(max_active))
    with _lock:
        if _active:
            raise RuntimeError("cannot reconfigure SSH runtime while active")
        MAX_ACTIVE = value
        _slots = threading.BoundedSemaphore(value)


def acquire(host: str, kind: str = "metrics") -> tuple[float, float]:
    """Acquire a process slot and return ``(started_at, wait_seconds)``."""
    global _active, _peak, _wait_total, _wait_count, _route_diagnostics
    wait_started = time.monotonic()
    _slots.acquire()
    started = time.monotonic()
    waited = started - wait_started
    now = time.time()
    with _lock:
        _active += 1
        _peak = max(_peak, _active)
        _started.append(now)
        _attempts[host] += 1
        _wait_total += waited
        _wait_count += 1
        if kind == "route":
            _route_diagnostics += 1
    return started, waited


def release(host: str, started: float, *, timeout: bool = False, error: bool = False) -> None:
    global _active, _duration_total, _duration_count
    duration = max(0.0, time.monotonic() - started)
    with _lock:
        _active = max(0, _active - 1)
        _duration_total += duration
        _duration_count += 1
        if timeout:
            _timeouts[host] += 1
        elif error:
            _errors[host] += 1
    _slots.release()


@contextmanager
def slot(host: str, kind: str = "metrics") -> Iterator[float]:
    started, waited = acquire(host, kind)
    outcome = {"timeout": False, "error": False}
    try:
        yield waited
    except TimeoutError:
        outcome["timeout"] = True
        raise
    except Exception:
        outcome["error"] = True
        raise
    finally:
        release(host, started, **outcome)


def note_result(host: str, *, timeout: bool = False, error: bool = False) -> None:
    """Record a handled subprocess failure (where no exception escaped)."""
    with _lock:
        if timeout:
            # A subprocess timeout escapes the slot context first and is then
            # translated by the caller.  Reclassify that handled error.
            if _errors[host] > 0:
                _errors[host] -= 1
            _timeouts[host] += 1
        elif error:
            _errors[host] += 1


def snapshot() -> dict[str, Any]:
    now = time.time()
    with _lock:
        while _started and _started[0] < now - 60.0:
            _started.popleft()
        return {
            "max_active_limit": MAX_ACTIVE,
            "active": _active,
            "peak_active": _peak,
            "connections_last_minute": len(_started),
            "attempts_by_host": dict(_attempts),
            "timeouts_by_host": dict(_timeouts),
            "errors_by_host": dict(_errors),
            "average_wait_ms": round(1000 * _wait_total / _wait_count, 1) if _wait_count else 0.0,
            "average_duration_ms": round(1000 * _duration_total / _duration_count, 1) if _duration_count else 0.0,
            "route_diagnostics": _route_diagnostics,
        }


def reset_for_tests() -> None:
    """Reset counters only; tests must not call while slots are occupied."""
    global _active, _peak, _wait_total, _wait_count, _duration_total, _duration_count, _route_diagnostics
    with _lock:
        _active = _peak = 0
        _wait_total = _duration_total = 0.0
        _wait_count = _duration_count = _route_diagnostics = 0
        _started.clear()
        _attempts.clear()
        _timeouts.clear()
        _errors.clear()
