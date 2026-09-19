"""OpenAI Codex subscription quota collector.

This collector surfaces the **ChatGPT / Codex subscription rate-limit
feed** — NOT OpenAI PAYG API usage. Three sources are tried, in order:

1. ``codex app-server`` JSON-RPC over stdio
   (``initialize`` + ``account/rateLimits/read``).
2. ``~/.codex/auth.json`` + ``GET /wham/usage`` on the ChatGPT backend
   (Win-CodexBar / CodexBar reference; MIT).
3. ``$CODEX_HOME/auth.json`` if ``CODEX_HOME`` is set.

Sources #1 and #2 share the same JSON shape:

    {
      "plan_type": "plus" | "pro" | "team" | ...,
      "rate_limit": {
        "primary_window":   {"used_percent": 2,  "limit_window_seconds": 18000,  "reset_at": ..., "reset_after_seconds": ...},
        "secondary_window": {"used_percent": 12, "limit_window_seconds": 604800, "reset_at": ..., "reset_after_seconds": ...},
        "code_review_window": {...} | null
      },
      "credits": {...} | null
    }

We never log or echo the OAuth ``access_token`` / ``refresh_token``.
The bearer token is loaded directly from the file by the collector and
only ever sent in the outgoing ``Authorization`` header.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .base import (
    PROVIDER_CODEX,
    ProviderResult,
    QuotaWindow,
    clamp_pct,
    epoch_sec_to_iso,
    now_iso,
    safe_float,
)

log = logging.getLogger("gpu_monitor.quotas.codex")

APP_NAME = "codex-app-server/gpu-monitor"
INIT_TIMEOUT_SEC = 8.0
CALL_TIMEOUT_SEC = 10.0
HTTP_TIMEOUT_SEC = 8.0

DEFAULT_WHAM_BASE = "https://chatgpt.com/backend-api"
WHAM_PATH = "/wham/usage"

# Codex CLI install locations (Windows).
_WINDOWS_FALLBACK_BINS = (
    r"C:\Program Files\OpenAI\Codex\codex.exe",
    r"C:\Program Files (x86)\OpenAI\Codex\codex.exe",
    str(Path.home() / ".codex" / "bin" / "codex.exe"),
    str(Path.home() / "AppData" / "Local" / "Programs" / "codex" / "codex.exe"),
)


def _resolve_codex_bin() -> str | None:
    found = shutil.which("codex")
    if found:
        return found
    for candidate in _WINDOWS_FALLBACK_BINS:
        if Path(candidate).is_file():
            return candidate
    return None


def _auth_path() -> Path | None:
    """Locate Codex's auth.json (env override first, then default)."""
    codex_home = os.environ.get("CODEX_HOME") or ""
    if codex_home.strip():
        p = Path(codex_home.strip()) / "auth.json"
        if p.is_file():
            return p
    default = Path.home() / ".codex" / "auth.json"
    if default.is_file():
        return default
    return None


def _load_codex_credentials() -> dict[str, Any] | None:
    """Read Codex credentials from auth.json. Tokens are never logged.

    Schema (matches Win-CodexBar):

        {
          "OPENAI_API_KEY": "...",   # optional
          "tokens": {
            "access_token":  "...",
            "refresh_token": "...",
            "account_id":    "..."
          }
        }

    We only return what we need; we never persist or echo the tokens.
    """
    p = _auth_path()
    if p is None:
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    api_key = data.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return {"kind": "api_key", "access_token": api_key.strip(),
                "account_id": None, "source": str(p)}

    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        access = tokens.get("access_token")
        if isinstance(access, str) and access.strip():
            return {
                "kind": "oauth",
                "access_token": access.strip(),
                "account_id": (
                    tokens.get("account_id")
                    if isinstance(tokens.get("account_id"), str)
                    else None
                ),
                "source": str(p),
            }
    return None


def _wham_fetch(creds: dict[str, Any]) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    base = DEFAULT_WHAM_BASE
    # Allow `chatgpt_base_url` override in config.toml (matches Win-CodexBar).
    cfg = Path.home() / ".codex" / "config.toml"
    if cfg.is_file():
        try:
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = line.split("#", 1)[0].strip()
                if not ln or "=" not in ln:
                    continue
                k, _, v = ln.partition("=")
                if k.strip() == "chatgpt_base_url":
                    val = v.strip().strip('"').strip("'")
                    if val.startswith("https://") and not val.endswith("/backend-api"):
                        val = val.rstrip("/") + "/backend-api"
                    if val:
                        base = val
        except OSError:
            pass

    url = base.rstrip("/") + WHAM_PATH
    headers = {
        "Authorization": f"Bearer {creds['access_token']}",
        "User-Agent": "GPUProfiler/1.0",
        "Accept": "application/json",
    }
    if creds.get("account_id"):
        headers["ChatGPT-Account-Id"] = creds["account_id"]

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# codex app-server (JSON-RPC over stdio) — preferred when CLI is available.
# ---------------------------------------------------------------------------


def _read_jsonl(stream, on_msg) -> None:
    buffer = ""
    for chunk in iter(lambda: stream.read(1), b""):
        if not chunk:
            return
        ch = chunk.decode("utf-8", errors="replace")
        if ch == "\n":
            line = buffer.strip()
            buffer = ""
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            on_msg(msg)
        else:
            buffer += ch


def _app_server_call(exe: str, method: str, *,
                     params: dict[str, Any] | None = None,
                     timeout_sec: float) -> dict[str, Any] | None:
    try:
        proc = subprocess.Popen(  # noqa: S603 — controlled inputs
            [exe, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        log.debug("codex: spawn failed: %s", exc)
        return None

    init_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "client": {"name": APP_NAME, "version": "1.0"},
            "capabilities": {},
        },
    }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    call_msg = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": method,
        "params": params or {},
    }
    payload = (
        json.dumps(init_msg) + "\n"
        + json.dumps(initialized) + "\n"
        + json.dumps(call_msg) + "\n"
    ).encode("utf-8")

    result_holder: dict[str, Any] = {}
    error_holder: dict[str, Any] = {}

    def on_msg(msg: dict[str, Any]) -> None:
        if msg.get("id") == 2:
            if "result" in msg:
                result_holder["v"] = msg["result"]
            elif "error" in msg:
                error_holder["v"] = msg["error"]

    reader = threading.Thread(
        target=_read_jsonl, args=(proc.stdout, on_msg), daemon=True
    )
    reader.start()

    try:
        if proc.stdin is None:
            return None
        proc.stdin.write(payload)
        proc.stdin.flush()
    except OSError as exc:
        log.debug("codex: stdin write failed: %s", exc)
        try:
            proc.kill()
        except OSError:
            pass
        return None

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if result_holder or error_holder:
            break
        if proc.poll() is not None and not reader.is_alive():
            break
        time.sleep(0.05)

    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass

    if error_holder:
        return {"_error": error_holder["v"]}
    return result_holder.get("v")


# ---------------------------------------------------------------------------
# Parser — handles both app-server result and wham/usage response.
# ---------------------------------------------------------------------------


def _id_for_duration(sec: int | None) -> str:
    """Pick canonical row id from limit_window_seconds."""
    if not sec:
        return "window"
    if sec == 18000:
        return "5h"
    if sec == 604800:
        return "7d"
    if sec in (2592000, 2678400, 2419200, 259200):
        return "monthly"
    # 1d, etc.
    hours = sec // 3600
    if hours and hours < 24:
        return f"{hours}h"
    days = sec // 86400
    if days:
        return f"{days}d"
    return f"{sec}s"


def _label_for_duration(sec: int | None) -> str:
    if not sec:
        return "Window"
    if sec == 18000:
        return "5h"
    if sec == 604800:
        return "7d"
    if sec in (2592000, 2678400, 2419200, 259200):
        return "Monthly"
    hours = sec // 3600
    if hours and hours < 24:
        return f"{hours}h"
    days = sec // 86400
    if days:
        return f"{days}d"
    return f"{sec}s"


# Friendly label for known non-standard slots (Codex returns e.g.
# code_review_window with no limit_window_seconds).
_POSITION_LABEL = {
    "primary": None,
    "secondary": None,
    "code_review": "Code review",
}


def _window_from_snapshot(slot: dict[str, Any], position: str) -> QuotaWindow | None:
    if not isinstance(slot, dict):
        return None
    used = safe_float(slot.get("used_percent"))
    win_sec_raw = slot.get("limit_window_seconds")
    win_sec = int(win_sec_raw) if isinstance(win_sec_raw, (int, float)) and win_sec_raw > 0 else None
    reset_at_raw = slot.get("reset_at")
    reset_at_iso = epoch_sec_to_iso(int(reset_at_raw)) if isinstance(reset_at_raw, (int, float)) and reset_at_raw > 0 else None
    reset_in_raw = slot.get("reset_after_seconds")
    reset_in = int(reset_in_raw) if isinstance(reset_in_raw, (int, float)) and reset_in_raw > 0 else None
    used_pct = clamp_pct(used)
    remaining_pct = (
        clamp_pct(100.0 - used_pct) if used_pct is not None else None
    )
    # Skip empty slots (no used_percent, no reset, no duration) — UI would
    # have nothing meaningful to render.
    if used_pct is None and reset_at_iso is None and reset_in is None and win_sec is None:
        return None
    duration_id = _id_for_duration(win_sec)
    label = _label_for_duration(win_sec)
    if position == "auto":
        # primary / secondary / etc.
        out_id = duration_id
    else:
        out_id = f"{position}_{duration_id}"
        if _POSITION_LABEL.get(position):
            label = _POSITION_LABEL[position]
    return QuotaWindow(
        id=out_id,
        label=label,
        window_seconds=win_sec,
        used_percent=used_pct,
        remaining_percent=remaining_pct,
        reset_at=reset_at_iso,
        reset_in_seconds=reset_in,
        unit="usage",
    )


def _parse(payload: Any) -> tuple[list[QuotaWindow], str | None, dict[str, Any] | None]:
    """Return (windows, plan_type, debug_meta)."""
    if not isinstance(payload, dict):
        return [], None, None

    rate = payload.get("rate_limit")
    out: list[QuotaWindow] = []
    if isinstance(rate, dict):
        primary = _window_from_snapshot(rate.get("primary_window") or {}, "auto")
        if primary is not None:
            out.append(primary)
        secondary = _window_from_snapshot(rate.get("secondary_window") or {}, "auto")
        if secondary is not None:
            out.append(secondary)
        cr = _window_from_snapshot(rate.get("code_review_window") or {}, "code_review")
        if cr is not None:
            out.append(cr)
    else:
        # Codex app-server sometimes returns rateLimits as {primary, secondary}
        rl = payload.get("rateLimits")
        if isinstance(rl, dict):
            for k in ("primary", "secondary"):
                w = _window_from_snapshot(rl.get(k) or {}, k)
                if w is not None:
                    out.append(w)
        elif isinstance(rl, list):
            for i, slot in enumerate(rl):
                w = _window_from_snapshot(slot if isinstance(slot, dict) else {}, f"win{i}")
                if w is not None:
                    out.append(w)

    plan_type = payload.get("plan_type")
    if not isinstance(plan_type, str):
        plan_type = None
    return out, plan_type, {"rate_limit_present": isinstance(rate, dict)}


def collect() -> ProviderResult:
    """Run a single Codex quota fetch attempt."""
    label = "Codex"

    # Path 1: codex app-server (CLI on PATH).
    exe = _resolve_codex_bin()
    if exe:
        try:
            res = _app_server_call(
                exe, "account/rateLimits/read",
                params=None, timeout_sec=CALL_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            res = None
            log.debug("codex app-server failed: %s", exc)
        if isinstance(res, dict):
            if "_error" in res:
                err = res["_error"]
                code = err.get("code") if isinstance(err, dict) else None
                msg = err.get("message") if isinstance(err, dict) else str(err)
                status = "rate_limited" if code in (-32005, 429, 529) else "not_logged_in"
                return ProviderResult(
                    provider=PROVIDER_CODEX,
                    status=status,
                    display_name=label,
                    source="codex app-server (account/rateLimits/read)",
                    last_error=msg[:200] if msg else f"code {code}",
                    debug=json.dumps(err)[:500] if not isinstance(err, str) else err[:500],
                    fetched_at_iso=now_iso(),
                )
            windows, plan, _ = _parse(res)
            if windows:
                return ProviderResult(
                    provider=PROVIDER_CODEX,
                    status="ok",
                    display_name=label,
                    source="codex app-server (account/rateLimits/read)",
                    plan_type=plan,
                    windows=windows,
                    last_success_iso=now_iso(),
                    fetched_at_iso=now_iso(),
                )
            return ProviderResult(
                provider=PROVIDER_CODEX,
                status="api_unavailable",
                display_name=label,
                source="codex app-server (account/rateLimits/read)",
                last_error="rateLimits missing",
                debug=json.dumps(res)[:500],
                fetched_at_iso=now_iso(),
            )

    # Path 2: ~/.codex/auth.json + /wham/usage on ChatGPT backend.
    creds = _load_codex_credentials()
    if creds is None:
        return ProviderResult(
            provider=PROVIDER_CODEX,
            status="cli_not_found",
            display_name=label,
            source="codex app-server / ~/.codex/auth.json",
            last_error="no Codex CLI and no auth.json",
            debug=f"checked PATH + {_WINDOWS_FALLBACK_BINS} and {Path.home() / '.codex' / 'auth.json'}",
            fetched_at_iso=now_iso(),
        )

    try:
        payload = _wham_fetch(creds)
    except Exception as exc:  # noqa: BLE001
        import urllib.error

        if isinstance(exc, urllib.error.HTTPError):
            if exc.code in (401, 403):
                return ProviderResult(
                    provider=PROVIDER_CODEX,
                    status="not_logged_in",
                    display_name=label,
                    source="~/.codex/auth.json (wham/usage)",
                    last_error=f"HTTP {exc.code}",
                    debug=f"chatgpt backend {WHAM_PATH}: {exc.reason}",
                    fetched_at_iso=now_iso(),
                )
            if exc.code in (429, 529):
                return ProviderResult(
                    provider=PROVIDER_CODEX,
                    status="rate_limited",
                    display_name=label,
                    source="~/.codex/auth.json (wham/usage)",
                    last_error=f"HTTP {exc.code}",
                    debug=f"chatgpt backend {WHAM_PATH}: {exc.reason}",
                    fetched_at_iso=now_iso(),
                )
            return ProviderResult(
                provider=PROVIDER_CODEX,
                status="api_unavailable",
                display_name=label,
                source="~/.codex/auth.json (wham/usage)",
                last_error=f"HTTP {exc.code}",
                debug=str(exc)[:200],
                fetched_at_iso=now_iso(),
            )
        return ProviderResult(
            provider=PROVIDER_CODEX,
            status="api_unavailable",
            display_name=label,
            source="~/.codex/auth.json (wham/usage)",
            last_error=str(exc)[:200] or exc.__class__.__name__,
            debug=repr(exc),
            fetched_at_iso=now_iso(),
        )

    windows, plan, _ = _parse(payload)
    if not windows:
        return ProviderResult(
            provider=PROVIDER_CODEX,
            status="api_unavailable",
            display_name=label,
            source="~/.codex/auth.json (wham/usage)",
            last_error="rate_limit missing",
            debug=json.dumps(payload)[:500],
            fetched_at_iso=now_iso(),
        )

    return ProviderResult(
        provider=PROVIDER_CODEX,
        status="ok",
        display_name=label,
        source="~/.codex/auth.json (wham/usage)",
        plan_type=plan,
        windows=windows,
        last_success_iso=now_iso(),
        fetched_at_iso=now_iso(),
    )
