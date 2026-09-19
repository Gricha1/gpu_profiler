"""Kimi Code / K3 quota collector.

Source: local `kimi web` server (default `127.0.0.1:58627`) which exposes
``GET /api/v1/oauth/usage`` behind a per-run bearer token written to
``~/.kimi-code/server.token``.

Live response shape (verified):

    {
      "code": 0,
      "data": {
        "kind": "ok",
        "quota": {
          "usages": {
            "limit5h":    {"usedRatio": 0.0,    "resetAt": "2026-09-15T18:12:41Z"},
            "monthTotal": {"usedRatio": 0.1899, "resetAt": "2026-10-15T00:00:00Z"},
            "monthCode":  {"usedRatio": 0.0,    "resetAt": "2026-10-15T00:00:00Z"}
          },
          "extraUsage": null
        }
      }
    }

The Kimi backend does **not** advertise `window_seconds` per usage slot
— only `usedRatio` and `resetAt`. We assign canonical IDs by parsing
the slot name (`limit5h`, `monthTotal`, `monthCode`, `limitWeek`, …) and
compute `window_seconds` heuristically from the slot's name when it
matches a known rolling window:

- ``limit5h`` / ``5h``         → 5h   (18000 s)
- ``limitWeek`` / ``week``     → 7d   (604800 s)
- ``monthTotal`` / ``monthly`` → 30d  (2592000 s)
- ``monthCode``                → 30d  (2592000 s)

We never invent windows the API didn't return. UI rows that map to no
window show "Not provided by provider".
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import (
    PROVIDER_KIMI,
    ProviderResult,
    QuotaWindow,
    clamp_pct,
    now_iso,
    safe_float,
)

log = logging.getLogger("gpu_monitor.quotas.kimi")

KIMI_DIR = Path.home() / ".kimi-code"
SERVER_TOKEN_PATH = KIMI_DIR / "server.token"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORTS = (58627, 53117, 52117, 51117)
HTTP_TIMEOUT_SEC = 4.0

# Map Kimi's slot keys to canonical (id, label, window_seconds).
_SLOT_MAP = {
    "limit5h":     ("5h",            "5h",      18000),
    "limit_week":  ("weekly",        "Weekly",  604800),
    "limitweek":   ("weekly",        "Weekly",  604800),
    "limit7d":     ("7d",            "7d",      604800),
    "monthtotal":  ("monthly_total", "Monthly total", 2592000),
    "monthly":     ("monthly_total", "Monthly total", 2592000),
    "monthcode":   ("monthly_code",  "Monthly code",  2592000),
}


def _read_server_token() -> str | None:
    try:
        if SERVER_TOKEN_PATH.is_file():
            tok = SERVER_TOKEN_PATH.read_text(encoding="utf-8", errors="replace").strip()
            if tok:
                return tok
    except OSError as exc:
        log.debug("kimi: cannot read %s: %s", SERVER_TOKEN_PATH, exc)
    return None


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _discover_port() -> int | None:
    for p in DEFAULT_PORTS:
        if _port_open(DEFAULT_HOST, p):
            return p
    return None


def _fetch_usage(host: str, port: int, token: str) -> dict[str, Any]:
    url = f"http://{host}:{port}/api/v1/oauth/usage"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "GPUProfiler/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _normalize_slot(key: str) -> tuple[str, str, int | None]:
    raw = key.strip()
    norm = raw.lower().replace("-", "_").replace(" ", "")
    if norm in _SLOT_MAP:
        return _SLOT_MAP[norm]
    # Fallback: keep the slot's own name; no window_seconds guess.
    return (raw, raw, None)


def _reset_in_seconds(reset_iso: str | None) -> int | None:
    if not reset_iso:
        return None
    try:
        s = reset_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))
    except (ValueError, TypeError):
        return None


def _parse(payload: dict[str, Any]) -> list[QuotaWindow]:
    out: list[QuotaWindow] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return out
    if data.get("kind") and data.get("kind") != "ok":
        return out
    quota = data.get("quota") or {}
    usages = quota.get("usages") or {}
    if not isinstance(usages, dict):
        return out

    for key, entry in usages.items():
        if not isinstance(entry, dict):
            continue
        used_ratio = safe_float(entry.get("usedRatio"))
        if used_ratio is None:
            continue
        win_id, label, win_sec = _normalize_slot(str(key))
        used_pct = clamp_pct(used_ratio * 100.0)
        remaining_pct = clamp_pct(100.0 - used_pct) if used_pct is not None else None
        reset_at = entry.get("resetAt")
        reset_at = reset_at if isinstance(reset_at, str) else None
        out.append(
            QuotaWindow(
                id=win_id,
                label=label,
                window_seconds=win_sec,
                used_percent=used_pct,
                remaining_percent=remaining_pct,
                reset_at=reset_at,
                reset_in_seconds=_reset_in_seconds(reset_at),
                unit="usage",
            )
        )
    return out


def collect() -> ProviderResult:
    """Run a single Kimi quota fetch attempt."""
    label = "Kimi K3"
    token = _read_server_token()
    if not token:
        return ProviderResult(
            provider=PROVIDER_KIMI,
            status="not_logged_in",
            display_name=label,
            source="/api/v1/oauth/usage (kimi web)",
            last_error="server.token missing",
            debug=f"expected {SERVER_TOKEN_PATH}",
            fetched_at_iso=now_iso(),
        )

    port = _discover_port()
    if port is None:
        return ProviderResult(
            provider=PROVIDER_KIMI,
            status="cli_not_found",
            display_name=label,
            source="/api/v1/oauth/usage (kimi web)",
            last_error="kimi web server not running",
            debug=f"probed {DEFAULT_HOST}:{DEFAULT_PORTS}",
            fetched_at_iso=now_iso(),
        )

    try:
        payload = _fetch_usage(DEFAULT_HOST, port, token)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        msg = "rate_limited" if exc.code in (429, 529) else "api_unavailable"
        return ProviderResult(
            provider=PROVIDER_KIMI,
            status=msg,
            display_name=label,
            source="/api/v1/oauth/usage (kimi web)",
            last_error=f"HTTP {exc.code}",
            debug=body or str(exc),
            fetched_at_iso=now_iso(),
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return ProviderResult(
            provider=PROVIDER_KIMI,
            status="api_unavailable",
            display_name=label,
            source="/api/v1/oauth/usage (kimi web)",
            last_error=str(exc)[:200] or exc.__class__.__name__,
            debug=repr(exc),
            fetched_at_iso=now_iso(),
        )

    windows = _parse(payload)
    if not windows:
        return ProviderResult(
            provider=PROVIDER_KIMI,
            status="api_unavailable",
            display_name=label,
            source="/api/v1/oauth/usage (kimi web)",
            last_error="empty usages",
            debug=json.dumps(payload)[:500],
            fetched_at_iso=now_iso(),
        )

    return ProviderResult(
        provider=PROVIDER_KIMI,
        status="ok",
        display_name=label,
        source="/api/v1/oauth/usage (kimi web)",
        plan_type="Plus",
        windows=windows,
        last_success_iso=now_iso(),
        fetched_at_iso=now_iso(),
    )
