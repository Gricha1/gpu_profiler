"""Aggregator + cache for AI Coding Quotas.

The collectors are independent. We run them concurrently on a background
thread so the request thread is never blocked, and we keep the last
*successful* snapshot per provider so the UI shows the previous values
with a `stale` flag when a refresh fails.

Refresh cadence
---------------
- Backend provider refresh: every 120s (within the 60–300s window the
  user asked for). `get_snapshot` triggers a background refresh when the
  cache is older than that.
- UI re-render / countdown: every 30s, served from cache — no extra
  upstream calls.

Authentication
--------------
The MiniMax API key is loaded from `.env` (already populated by
`app.py._load_dotenv`). The in-UI "Settings" sheet writes/clears the
key in `.env` and the runtime env so subsequent calls pick it up
without restart. `MINIMAX_GROUP_ID` was required by an older (now
defunct) Win-CodexBar endpoint and is no longer read at runtime.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import codex, kimi, minimax
from .base import (
    ALL_PROVIDERS,
    STATUS_OK,
    STATUS_STALE,
    ProviderResult,
)

log = logging.getLogger("gpu_monitor.quotas")

MIN_REFRESH_SEC = 60.0
MAX_REFRESH_SEC = 300.0
DEFAULT_REFRESH_SEC = 120.0

_COLLECTORS = {
    "kimi": kimi.collect,
    "minimax": minimax.collect,
    "codex": codex.collect,
}

_lock = threading.Lock()
_results: dict[str, ProviderResult] = {}
_last_full_refresh_ts: float = 0.0
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="quotas")


def _run_one(provider: str) -> ProviderResult:
    fn = _COLLECTORS.get(provider)
    if fn is None:
        return ProviderResult(
            provider=provider,
            status="error",
            display_name=provider,
            last_error="unknown provider",
            fetched_at_iso=None,
        )
    t0 = time.perf_counter()
    try:
        res = fn()
    except Exception as exc:  # noqa: BLE001
        log.exception("quotas: collector %s crashed", provider)
        return ProviderResult(
            provider=provider,
            status="error",
            display_name=provider,
            last_error=exc.__class__.__name__,
            debug=repr(exc),
            fetched_at_iso=None,
        )
    finally:
        log.debug("quotas: %s took %.0fms", provider, (time.perf_counter() - t0) * 1000)
    return res


def _refresh_all() -> dict[str, ProviderResult]:
    out: dict[str, ProviderResult] = {}
    futs = {p: _executor.submit(_run_one, p) for p in ALL_PROVIDERS}
    for p, fut in futs.items():
        try:
            out[p] = fut.result(timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            out[p] = ProviderResult(
                provider=p,
                status="error",
                display_name=p,
                last_error=exc.__class__.__name__,
                debug=repr(exc),
            )
    return out


def refresh_now() -> dict[str, ProviderResult]:
    """Force a refresh of all collectors and update the cache."""
    global _last_full_refresh_ts
    fresh = _refresh_all()
    with _lock:
        for p, res in fresh.items():
            prev = _results.get(p)
            if res.status == STATUS_OK or prev is None:
                _results[p] = res
            elif prev.last_success_iso:
                # Preserve last good windows; mark as stale.
                merged = ProviderResult(
                    provider=p,
                    status=STATUS_STALE,
                    display_name=res.display_name,
                    windows=prev.windows,
                    source=res.source,
                    plan_type=res.plan_type,
                    last_success_iso=prev.last_success_iso,
                    last_error=res.last_error,
                    debug=res.debug,
                    fetched_at_iso=res.fetched_at_iso,
                    stale=True,
                )
                _results[p] = merged
            else:
                # First refresh and it failed — show the real failure, not "stale".
                _results[p] = res
        _last_full_refresh_ts = time.time()
    return deepcopy(_results)


def get_snapshot() -> dict[str, ProviderResult]:
    """Return the cached snapshot, refreshing in the background if stale."""
    with _lock:
        age = time.time() - _last_full_refresh_ts
    if not _results or age >= DEFAULT_REFRESH_SEC:
        # Background refresh — never block the request thread.
        _executor.submit(refresh_now)
    with _lock:
        return deepcopy(_results)


def get_minimal_config() -> dict[str, Any]:
    """Return a UI-friendly config summary (no secrets)."""
    return {
        "minimax": {
            "api_key_set": bool(os.environ.get("MINIMAX_API_KEY")),
        },
        "kimi": {
            "server_token_present": _kimi_token_present(),
            "default_ports": list(getattr(kimi, "DEFAULT_PORTS", ())),
        },
        "codex": {
            "cli_path": codex._resolve_codex_bin(),
            "auth_path": str(codex._auth_path()) if codex._auth_path() else None,
        },
        "refresh_sec": DEFAULT_REFRESH_SEC,
        "min_refresh_sec": MIN_REFRESH_SEC,
        "max_refresh_sec": MAX_REFRESH_SEC,
    }


def _kimi_token_present() -> bool:
    p = Path.home() / ".kimi-code" / "server.token"
    try:
        return p.is_file() and bool(p.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def set_minimax_config(api_key: str | None) -> dict[str, Any]:
    """Stash the new MiniMax API key in the runtime env and persist to `.env`.

    The key is stored only on the local machine; it is never logged and is
    never returned in API responses.

    An empty string is treated as "clear this key" (the corresponding line
    is removed from `.env` and the env var is unset).
    """
    def _norm(v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v if v else None  # empty string -> None (clear)

    api_key = _norm(api_key)

    if api_key is None:
        os.environ.pop("MINIMAX_API_KEY", None)
    else:
        os.environ["MINIMAX_API_KEY"] = api_key

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    out_lines: list[str] = []
    replaced: set[str] = set()
    for raw in lines:
        line = raw.rstrip("\r")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            out_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key == "MINIMAX_API_KEY":
            replaced.add(key)
            if api_key is not None:
                out_lines.append(f"MINIMAX_API_KEY={api_key}")
            # else: drop the line (cleared)
            continue
        out_lines.append(line)
    if api_key is not None and "MINIMAX_API_KEY" not in replaced:
        out_lines.append(f"MINIMAX_API_KEY={api_key}")

    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "applied": {
            "api_key": bool(api_key),
        },
        "cleared": {
            "api_key": api_key is None,
        },
    }
