"""Mesh overlay health (NetBird / ZeroTier) — diagnose only, no route mutation."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

CACHE_SEC = 15.0
CONNECT_TIMEOUT = 2.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "data": None}

# Representative probes (must stay on overlay IPs from ssh config / host_paths)
NETBIRD_PROBES = [
    {"name": "aicenter2", "host": "100.98.59.202", "port": 22},
    {"name": "aicenteritl", "host": "100.98.50.236", "port": 22},
]
ZEROTIER_PROBES = [
    {"name": "lab_comp_zt", "host": "10.43.71.7", "port": 22},
]


def _tcp(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.perf_counter() - t0) * 1000.0
            return {"ok": True, "ms": round(ms, 1), "error": None}
    except OSError as exc:
        # WSAEACCES on Windows often surfaces as PermissionError / WinError 10013
        err = str(exc)
        code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
        blocked = code == 10013 or "10013" in err or "forbidden" in err.lower()
        return {
            "ok": False,
            "ms": None,
            "error": err,
            "wsaeacces": blocked,
            "winerror": code,
        }


def _iface_for(ip: str) -> str | None:
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Find-NetRoute -RemoteIPAddress {ip} -EA SilentlyContinue |"
                f" Select-Object -First 1 -ExpandProperty InterfaceAlias)",
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
            encoding="utf-8",
            errors="replace",
        )
        alias = (out.stdout or "").strip()
        return alias or None
    except Exception:
        return None


def _amnezia_tun_up() -> bool:
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-NetAdapter -Name tun2 -EA SilentlyContinue | Where-Object Status -eq 'Up').Count",
            ],
            capture_output=True,
            text=True,
            timeout=3.0,
            encoding="utf-8",
            errors="replace",
        )
        return int((out.stdout or "0").strip() or "0") > 0
    except Exception:
        return False


def _probe_group(probes: list[dict[str, Any]], expect_iface_substr: str) -> dict[str, Any]:
    results = []
    any_ok = False
    any_wsae = False
    for p in probes:
        r = _tcp(p["host"], int(p["port"]))
        iface = _iface_for(p["host"])
        row = {
            "name": p["name"],
            "host": p["host"],
            "port": p["port"],
            "iface": iface,
            **r,
        }
        if r.get("ok"):
            any_ok = True
        if r.get("wsaeacces"):
            any_wsae = True
        results.append(row)

    if any_ok:
        status = "OK"
    elif any_wsae:
        status = "BLOCKED"
    else:
        status = "DOWN"

    hint = None
    if status == "BLOCKED":
        hint = (
            "WSAEACCES: Amnezia kill-switch WFP blocks overlay. "
            "Enable Amnezia site split-tunnel ExceptSites for mesh CIDRs "
            "(scripts/amnezia/configure_split_tunnel.ps1), then Disconnect/Connect."
        )
    elif status == "DOWN":
        hint = "Overlay peer unreachable (NetBird/ZeroTier peer offline or path down)."

    return {
        "status": status,
        "expect_iface": expect_iface_substr,
        "probes": results,
        "hint": hint,
    }


def get_mesh_health(*, force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _lock:
        cached = _cache.get("data")
        if not force and cached and now - float(_cache.get("ts") or 0.0) < CACHE_SEC:
            out = dict(cached)
            out["cached"] = True
            return out

        netbird = _probe_group(NETBIRD_PROBES, "wt0")
        zerotier = _probe_group(ZEROTIER_PROBES, "ZeroTier")
        data = {
            "ok": True,
            "cached": False,
            "ts": now,
            "amnezia_tun2_up": _amnezia_tun_up(),
            "netbird": netbird,
            "zerotier": zerotier,
            "summary": {
                "netbird": netbird["status"],
                "zerotier": zerotier["status"],
            },
        }
        _cache["ts"] = now
        _cache["data"] = data
        return dict(data)
