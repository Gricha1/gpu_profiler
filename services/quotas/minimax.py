"""MiniMax M3 Token Plan quota collector.

Endpoint (no group_id required):

    GET https://www.minimax.io/v1/token_plan/remains
    Authorization: Bearer <sk-cp-…>
    Content-Type: application/json
    MM-API-Source: gpu-monitor

Response shape (verified against the live endpoint):

    {
      "model_remains": [
        {
          "start_time": 1789484400000,            # ms epoch — interval start
          "end_time":   1789502400000,            # ms epoch — interval end (5h later)
          "remains_time": 13091051,               # ms until reset
          "current_interval_total_count":   0,    # provider-supplied totals
          "current_interval_usage_count":   0,    # (often zero — billing-by-Credit)
          "model_name": "general",
          "current_weekly_total_count":    0,
          "current_weekly_usage_count":    0,
          "weekly_start_time": 1789344000000,
          "weekly_end_time":   1789948800000,
          "weekly_remains_time": 459491051,
          "current_interval_status": 1,          # 1 = active, 2 = expired, 3 = inactive
          "current_interval_remaining_percent": 68,
          "current_weekly_status": 1,
          "current_weekly_remaining_percent": 81
        },
        {
          "model_name": "video",
          "current_interval_status": 3,          # inactive for this key
          "current_interval_remaining_percent": 100,
          ...
        }
      ],
      "base_resp": {"status_code": 0, "status_msg": "success"}
    }

The collector surfaces one pair of windows per *active* model (status != 3
and remaining != 100):

- ``5h`` (interval) with end_time as reset
- ``Weekly`` (weekly) with weekly_end_time as reset

Both `used_percent` and `remaining_percent` are derived only from the
values the API actually returns — no fabrication.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from .base import (
    PROVIDER_MINIMAX,
    ProviderResult,
    QuotaWindow,
    clamp_pct,
    epoch_ms_to_iso,
    now_iso,
    safe_float,
)

log = logging.getLogger("gpu_monitor.quotas.minimax")

DEFAULT_BASE_URL = "https://www.minimax.io"
ENDPOINT_PATH = "/v1/token_plan/remains"
USER_AGENT_SOURCE = "gpu-monitor"
HTTP_TIMEOUT_SEC = 8.0

_OK = 0


def _load_config() -> str | None:
    return os.environ.get("MINIMAX_API_KEY") or None


def _http_get(base_url: str, path: str, api_key: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "MM-API-Source": USER_AGENT_SOURCE,
            "User-Agent": "GPUProfiler/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _check_base_resp(payload: dict[str, Any]) -> str | None:
    base = payload.get("base_resp") if isinstance(payload, dict) else None
    if not isinstance(base, dict):
        return None
    code = base.get("status_code")
    if code in (None, _OK):
        return None
    return str(base.get("status_msg") or f"base_resp.status_code={code}")


def _window_seconds(start_ms: int | None, end_ms: int | None) -> int | None:
    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        return None
    delta = end_ms - start_ms
    if delta <= 0:
        return None
    return delta // 1000


def _build_window(
    *,
    win_id: str,
    label: str,
    remaining_pct: float | None,
    start_ms: int | None,
    end_ms: int | None,
    reset_in_ms: int | None,
    status: int | None,
) -> QuotaWindow | None:
    """Translate a single (interval or weekly) MiniMax slot into QuotaWindow.

    Returns ``None`` if the slot is inactive (status == 3) AND has no
    remaining data we trust.
    """
    if status == 3:
        return None
    used = (
        clamp_pct(100.0 - remaining_pct) if remaining_pct is not None else None
    )
    if used is None and status in (None, 2):
        # Active but no data — skip rather than fabricate.
        return None
    win_sec = _window_seconds(start_ms, end_ms)
    reset_at = epoch_ms_to_iso(end_ms)
    reset_in = (
        int(reset_in_ms // 1000)
        if isinstance(reset_in_ms, int) and reset_in_ms > 0
        else None
    )
    return QuotaWindow(
        id=win_id,
        label=label,
        window_seconds=win_sec,
        used_percent=used,
        remaining_percent=clamp_pct(remaining_pct),
        reset_at=reset_at,
        reset_in_seconds=reset_in,
        unit="usage",
    )


def _parse(payload: dict[str, Any]) -> list[QuotaWindow]:
    items = payload.get("model_remains")
    if not isinstance(items, list) or not items:
        return []

    items_sorted = sorted(
        (i for i in items if isinstance(i, dict)),
        key=lambda i: (0 if i.get("model_name") == "general" else 1,
                       str(i.get("model_name") or "")),
    )

    out: list[QuotaWindow] = []
    for it in items_sorted:
        model = str(it.get("model_name") or "model")

        interval = _build_window(
            win_id=f"{model}_5h",
            label=f"{model} 5h",
            remaining_pct=safe_float(it.get("current_interval_remaining_percent")),
            start_ms=it.get("start_time"),
            end_ms=it.get("end_time"),
            reset_in_ms=it.get("remains_time"),
            status=it.get("current_interval_status"),
        )
        if interval is not None:
            out.append(interval)

        weekly = _build_window(
            win_id=f"{model}_weekly",
            label=f"{model} Weekly",
            remaining_pct=safe_float(it.get("current_weekly_remaining_percent")),
            start_ms=it.get("weekly_start_time"),
            end_ms=it.get("weekly_end_time"),
            reset_in_ms=it.get("weekly_remains_time"),
            status=it.get("current_weekly_status"),
        )
        if weekly is not None:
            out.append(weekly)

    return out


def collect() -> ProviderResult:
    """Run a single MiniMax quota fetch attempt."""
    label = "MiniMax M3"
    api_key = _load_config()

    if not api_key:
        return ProviderResult(
            provider=PROVIDER_MINIMAX,
            status="not_configured",
            display_name=label,
            source=ENDPOINT_PATH,
            last_error="MINIMAX_API_KEY not set",
            debug="set in .env or via UI settings (⚙ in AI Coding Quotas panel)",
            fetched_at_iso=now_iso(),
        )

    try:
        payload = _http_get(DEFAULT_BASE_URL, ENDPOINT_PATH, api_key)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        if exc.code in (401, 403):
            return ProviderResult(
                provider=PROVIDER_MINIMAX,
                status="api_unavailable",
                display_name=label,
                source=ENDPOINT_PATH,
                last_error=f"HTTP {exc.code} (auth)",
                debug=f"{DEFAULT_BASE_URL}{ENDPOINT_PATH}: {body or exc.reason}",
                fetched_at_iso=now_iso(),
            )
        if exc.code in (429, 529):
            return ProviderResult(
                provider=PROVIDER_MINIMAX,
                status="rate_limited",
                display_name=label,
                source=ENDPOINT_PATH,
                last_error=f"HTTP {exc.code}",
                debug=f"{DEFAULT_BASE_URL}{ENDPOINT_PATH}: {body or exc.reason}",
                fetched_at_iso=now_iso(),
            )
        return ProviderResult(
            provider=PROVIDER_MINIMAX,
            status="api_unavailable",
            display_name=label,
            source=ENDPOINT_PATH,
            last_error=f"HTTP {exc.code}",
            debug=f"{DEFAULT_BASE_URL}{ENDPOINT_PATH}: {body or exc.reason}",
            fetched_at_iso=now_iso(),
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return ProviderResult(
            provider=PROVIDER_MINIMAX,
            status="api_unavailable",
            display_name=label,
            source=ENDPOINT_PATH,
            last_error=str(exc)[:200] or exc.__class__.__name__,
            debug=repr(exc),
            fetched_at_iso=now_iso(),
        )

    err = _check_base_resp(payload)
    if err:
        # 200 OK with status_code != 0 means "key valid but not associated
        # with a paid plan on the openplatform". Report as api_unavailable
        # so the user knows the key itself is OK.
        return ProviderResult(
            provider=PROVIDER_MINIMAX,
            status="api_unavailable",
            display_name=label,
            source=ENDPOINT_PATH,
            last_error=err,
            debug=f"{DEFAULT_BASE_URL}{ENDPOINT_PATH}",
            fetched_at_iso=now_iso(),
        )

    windows = _parse(payload)
    if not windows:
        return ProviderResult(
            provider=PROVIDER_MINIMAX,
            status="api_unavailable",
            display_name=label,
            source=ENDPOINT_PATH,
            last_error="no active quota windows",
            debug="model_remains returned no active intervals",
            fetched_at_iso=now_iso(),
        )

    return ProviderResult(
        provider=PROVIDER_MINIMAX,
        status="ok",
        display_name=label,
        source=ENDPOINT_PATH,
        plan_type="Token Plan",
        windows=windows,
        last_success_iso=now_iso(),
        fetched_at_iso=now_iso(),
    )
