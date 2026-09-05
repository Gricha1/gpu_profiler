"""Read-only Mesh Route Watcher status for GPU Profiler UI."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "runtime" / "mesh_route_watcher_status.json"
PID_FILE = ROOT / "runtime" / "mesh_route_watcher.pid"
TASK_NAME = "GPUProfiler-MeshRouteWatcher"
STALE_SEC = 90.0


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # PowerShell ToString('o') may emit 7 fractional digits; fromisoformat wants <=6
        if "." in s:
            head, rest = s.split(".", 1)
            digits = ""
            tz = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    digits += ch
                else:
                    tz = rest[i:]
                    break
            digits = (digits + "000000")[:6]
            s = f"{head}.{digits}{tz}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"try {{ Get-Process -Id {int(pid)} -EA Stop | Out-Null; '1' }} catch {{ '0' }}",
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
            encoding="utf-8",
            errors="replace",
        )
        return (out.stdout or "").strip() == "1"
    except Exception:
        return False


def _task_info() -> dict[str, Any]:
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"""
$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -EA SilentlyContinue
if (-not $t) {{ 'NOT_INSTALLED|'; exit }}
$i = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
$state = [string]$t.State
$enabled = if ($t.Settings.Enabled) {{ 'Enabled' }} else {{ 'Disabled' }}
Write-Output ($state + '|' + $enabled)
""",
            ],
            capture_output=True,
            text=True,
            timeout=6.0,
            encoding="utf-8",
            errors="replace",
        )
        line = (out.stdout or "").strip().splitlines()
        line = line[-1] if line else "NOT_INSTALLED|"
        parts = line.split("|", 1)
        state = parts[0] if parts else "NOT_INSTALLED"
        enabled = parts[1] if len(parts) > 1 else ""
        if state == "NOT_INSTALLED":
            return {"installed": False, "task_scheduler": "NOT INSTALLED", "task_state": None}
        return {
            "installed": True,
            "task_scheduler": enabled or "Unknown",
            "task_state": state,
        }
    except Exception as exc:
        return {
            "installed": False,
            "task_scheduler": "ERROR",
            "task_state": None,
            "task_error": str(exc),
        }


def _read_status_file() -> dict[str, Any]:
    if not STATUS_PATH.is_file():
        return {}
    try:
        text = STATUS_PATH.read_text(encoding="utf-8-sig")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_watcher_status() -> dict[str, Any]:
    task = _task_info()
    raw = _read_status_file()
    pid = raw.get("pid")
    try:
        pid = int(pid) if pid is not None else None
    except Exception:
        pid = None
    if pid is None and PID_FILE.is_file():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip().splitlines()[0])
        except Exception:
            pid = None

    alive = _pid_alive(pid)
    ts = _parse_iso(raw.get("ts"))
    age = (time.time() - ts) if ts else None
    stale = age is not None and age > STALE_SEC

    if not task.get("installed"):
        ui_status = "STOPPED"
        task_label = "NOT INSTALLED"
    elif str(task.get("task_scheduler", "")).lower() == "disabled":
        ui_status = "STOPPED"
        task_label = "Disabled"
    elif alive and not stale and raw.get("running", True):
        ui_status = "RUNNING"
        task_label = task.get("task_scheduler") or "Enabled"
    elif alive and stale:
        ui_status = "STALE"
        task_label = task.get("task_scheduler") or "Enabled"
    elif task.get("installed") and not alive:
        ui_status = "ERROR"
        task_label = task.get("task_scheduler") or "Enabled"
    else:
        ui_status = "STOPPED"
        task_label = task.get("task_scheduler") or "Unknown"

    def _fmt(ts_s: str | None) -> str | None:
        if not ts_s:
            return None
        try:
            # Prefer local HH:MM:SS for UI
            p = _parse_iso(ts_s)
            if p is None:
                return ts_s
            return datetime.fromtimestamp(p).strftime("%H:%M:%S")
        except Exception:
            return ts_s

    return {
        "ok": True,
        "status": ui_status,
        "task_scheduler": task_label,
        "task_name": TASK_NAME,
        "task_state": task.get("task_state"),
        "pid": pid if alive else pid,
        "process_alive": alive,
        "heartbeat_age_sec": round(age, 1) if age is not None else None,
        "stale": bool(stale),
        "last_check": _fmt(raw.get("last_check")),
        "last_fix": _fmt(raw.get("last_fix")),
        "last_check_iso": raw.get("last_check"),
        "last_fix_iso": raw.get("last_fix"),
        "last_event": raw.get("last_event"),
        "amnezia_up": raw.get("amnezia_up"),
        "netbird_route": str(raw.get("netbird_route") or "unknown").upper(),
        "zerotier_route": str(raw.get("zerotier_route") or "unknown").upper(),
        "last_error": raw.get("last_error"),
        "last_removed": raw.get("last_removed"),
        "status_file": str(STATUS_PATH),
    }
