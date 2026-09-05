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
ZT_CLI = Path(r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat")

_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "data": None}

# Representative probes (must stay on overlay IPs from ssh config / host_paths)
NETBIRD_PROBES = [
    {"name": "aicenter2", "host": "100.98.59.202", "port": 22},
    {"name": "aicenteritl", "host": "100.98.50.236", "port": 22},
]
# Direct Windows→10.43.71.7 is black-holed; lab_comp uses ProxyJump cds2 (NetBird 100.98.2.11).
# Keep optional peer probes for info, but local ZT membership drives OK/DOWN.
ZEROTIER_PEER_PROBES = [
    {"name": "lab_comp_direct", "host": "10.43.71.7", "port": 22},
    {"name": "cds2_old_zt", "host": "10.43.71.57", "port": 22},
]
# Jump path that actually reaches lab_comp (NetBird, not ZT) — reported separately
LAB_JUMP_PROBE = {"name": "cds2_netbird_jump", "host": "100.98.2.11", "port": 22}


def _tcp(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.perf_counter() - t0) * 1000.0
            return {"ok": True, "ms": round(ms, 1), "error": None}
    except OSError as exc:
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


def _zerotier_local() -> dict[str, Any]:
    """cds_team membership / assigned 10.43.71 IP — true ZT client health."""
    if not ZT_CLI.is_file():
        return {"ok": False, "networks": [], "error": "zerotier-cli missing"}
    try:
        out = subprocess.run(
            [str(ZT_CLI), "listnetworks"],
            capture_output=True,
            text=True,
            timeout=5.0,
            encoding="utf-8",
            errors="replace",
        )
        lines = [
            ln
            for ln in (out.stdout or "").splitlines()
            if ln.startswith("200 listnetworks ") and "<nwid>" not in ln
        ]
        nets = []
        cds_ok = False
        for ln in lines:
            parts = ln.split()
            # 200 listnetworks <nwid> <name...> <mac> <status> <type> <dev> <ips|->
            if len(parts) < 8:
                continue
            nwid = parts[2]
            # name can contain spaces — status is one of OK/ACCESS_DENIED/REQUESTING/...
            status = None
            ips = parts[-1]
            for tok in parts[3:]:
                if tok in {
                    "OK",
                    "ACCESS_DENIED",
                    "NOT_FOUND",
                    "REQUESTING_CONFIGURATION",
                    "PORT_ERROR",
                    "CLIENT_TOO_OLD",
                }:
                    status = tok
                    break
            row = {"nwid": nwid, "status": status or "?", "ips": ips if ips != "-" else None}
            nets.append(row)
            if status == "OK" and row["ips"] and row["ips"].startswith("10.43.71."):
                cds_ok = True
            # also accept named cds_team
            if status == "OK" and "cds" in ln.lower() and row["ips"]:
                cds_ok = True
        return {"ok": cds_ok, "networks": nets, "error": None}
    except Exception as exc:
        return {"ok": False, "networks": [], "error": str(exc)}


def _probe_rows(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for p in probes:
        r = _tcp(p["host"], int(p["port"]))
        results.append(
            {
                "name": p["name"],
                "host": p["host"],
                "port": p["port"],
                "iface": _iface_for(p["host"]),
                **r,
            }
        )
    return results


def _probe_group(probes: list[dict[str, Any]], expect_iface_substr: str) -> dict[str, Any]:
    results = _probe_rows(probes)
    any_ok = any(r.get("ok") for r in results)
    any_wsae = any(r.get("wsaeacces") for r in results)

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
            "Add mesh CIDRs to Amnezia ExceptSites via AmneziaVPN UI "
            "(VpnAllExceptSites), then Disconnect/Connect."
        )
    elif status == "DOWN":
        hint = "Overlay peer unreachable (NetBird/ZeroTier peer offline or path down)."

    return {
        "status": status,
        "expect_iface": expect_iface_substr,
        "probes": results,
        "hint": hint,
    }


def _zerotier_health() -> dict[str, Any]:
    local = _zerotier_local()
    peers = _probe_rows(ZEROTIER_PEER_PROBES)
    jump = _probe_rows([LAB_JUMP_PROBE])[0]
    peer_ok = any(r.get("ok") for r in peers)
    peer_wsae = any(r.get("wsaeacces") for r in peers)

    if peer_wsae and not local.get("ok"):
        status = "BLOCKED"
        hint = (
            "WSAEACCES on ZT peers: Amnezia kill-switch. "
            "Add 10.43.71.0/24 in AmneziaVPN UI ExceptSites; then Disconnect/Connect."
        )
    elif local.get("ok"):
        # Local cds_team is enough for "ZT mesh client OK".
        # Direct 10.43.71.7 is often black-holed; lab_comp works via ProxyJump cds2 (NetBird).
        status = "OK"
        if peer_ok:
            hint = None
        elif jump.get("ok"):
            hint = (
                "ZT local OK. Direct 10.43.71.7 timed out (known black-hole). "
                "lab_comp reachable via ProxyJump cds2 (NetBird 100.98.2.11)."
            )
        else:
            hint = (
                "ZT local OK (cds_team), but no direct ZT peer answered SSH. "
                "lab_comp normally uses ProxyJump cds2."
            )
    elif peer_ok:
        status = "OK"
        hint = None
    elif peer_wsae:
        status = "BLOCKED"
        hint = "WSAEACCES on ZT peers (Amnezia kill-switch)."
    else:
        status = "DOWN"
        hint = "ZeroTier cds_team not OK / no assigned 10.43.71 IP."

    return {
        "status": status,
        "expect_iface": "ZeroTier",
        "local": local,
        "probes": peers,
        "lab_jump": jump,
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
        zerotier = _zerotier_health()
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
