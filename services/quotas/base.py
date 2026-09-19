"""Common types and helpers for the AI Coding Quotas collectors.

The normalized response contract (sent to the UI as JSON) is:

    {
      "ok": true,
      "providers": {
        "<provider>": {
          "id": "minimax" | "kimi" | "codex",
          "display_name": "MiniMax M3",
          "status": "ok" | "not_configured" | "not_logged_in"
                   | "cli_not_found" | "api_unavailable"
                   | "rate_limited" | "stale" | "error",
          "source": "human-readable description of where data came from",
          "plan_type": "Plus" | null,
          "stale": false,
          "updated_at": "2026-09-15T18:53:14+03:00",
          "windows": [
            {
              "id": "5h",
              "label": "5h",
              "window_seconds": 18000,
              "used_percent": 29.0,
              "remaining_percent": 71.0,
              "reset_at": "2026-09-15T20:00:00Z" | null,
              "reset_in_seconds": 3600 | null,
              "unit": "usage"
            }
          ],
          "last_error": null
        }
      }
    }

`window_seconds == 0` means "the provider did not return a duration for
this window" (rare; we generally have it). UI falls back to the
provider-supplied `id`/`label` in that case.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# Provider identifiers (stable for the UI / API contract).
PROVIDER_KIMI = "kimi"
PROVIDER_MINIMAX = "minimax"
PROVIDER_CODEX = "codex"

ALL_PROVIDERS = (PROVIDER_KIMI, PROVIDER_MINIMAX, PROVIDER_CODEX)

# Status strings surfaced to the UI. Keep the set closed and stable.
STATUS_OK = "ok"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_NOT_LOGGED_IN = "not_logged_in"
STATUS_CLI_NOT_FOUND = "cli_not_found"
STATUS_API_UNAVAILABLE = "api_unavailable"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_STALE = "stale"
STATUS_ERROR = "error"

# Canonical "standard" rows the UI shows for every provider, in order.
# Each maps the row key to the list of window_seconds values that should
# populate it. First match wins.
STANDARD_ROWS = (
    ("5h", (18000,)),
    ("7d", (604800,)),
    ("Monthly", (2592000, 2678400, 259200, 2419200)),  # 30d / 31d / monthly variants
)


@dataclass
class QuotaWindow:
    """One rolling rate-limit window returned by a provider.

    All percentages are 0..100. `used_percent == None` means the
    provider did not return the value — the UI renders "Not provided".
    """

    id: str  # canonical id, e.g. "5h", "weekly", "monthly_total"
    label: str  # human-readable label, may differ from id
    window_seconds: int | None = None
    used_percent: float | None = None
    remaining_percent: float | None = None
    reset_at: str | None = None  # ISO 8601 UTC
    reset_in_seconds: int | None = None
    unit: str = "usage"  # "usage" | "credits"

    def to_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "label": self.label,
            "window_seconds": self.window_seconds,
            "used_percent": (
                round(float(self.used_percent), 2)
                if isinstance(self.used_percent, (int, float))
                else None
            ),
            "remaining_percent": (
                round(float(self.remaining_percent), 2)
                if isinstance(self.remaining_percent, (int, float))
                else None
            ),
            "reset_at": self.reset_at,
            "reset_in_seconds": self.reset_in_seconds,
            "unit": self.unit,
        }
        return out


@dataclass
class ProviderResult:
    """Snapshot for one provider."""

    provider: str
    status: str
    display_name: str
    windows: list[QuotaWindow] = field(default_factory=list)
    source: str | None = None  # short description of the data source
    plan_type: str | None = None  # e.g. "Plus", "Pro", "Free"
    last_success_iso: str | None = None
    last_error: str | None = None  # short, safe message for the UI
    debug: str | None = None  # verbose detail; only returned when ?debug=1
    fetched_at_iso: str | None = None  # timestamp of THIS fetch attempt
    stale: bool = False  # true when result is older than refresh interval

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.provider,
            "display_name": self.display_name,
            "status": self.status,
            "source": self.source,
            "plan_type": self.plan_type,
            "stale": self.stale,
            "windows": [w.to_dict() for w in self.windows],
            "updated_at": self.last_success_iso,
            "last_error": self.last_error,
        }
        if debug:
            out["debug"] = self.debug
            out["fetched_at_iso"] = self.fetched_at_iso
        return out


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def epoch_ms_to_iso(ms: int | None) -> str | None:
    if not isinstance(ms, int) or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return None


def epoch_sec_to_iso(sec: int | None) -> str | None:
    if not isinstance(sec, int) or sec <= 0:
        return None
    try:
        return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return None


def clamp_pct(p: float | None) -> float | None:
    if p is None:
        return None
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(max(0.0, min(100.0, v)), 2)


def safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def redact(s: str | None, keep: int = 4) -> str:
    """Redact a secret-like string. Keeps first/last `keep` chars only.

    Used in any error / debug message that might mention a token by accident.
    """
    if not s:
        return ""
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}…{s[-keep:]}"


def window_id_for_seconds(window_seconds: int | None) -> str | None:
    """Pick a canonical id for the standard UI rows by duration."""
    if not window_seconds:
        return None
    for row_id, durations in STANDARD_ROWS:
        if window_seconds in durations:
            return row_id
    return None
