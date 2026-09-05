"""Public IP / network mode status with short-lived cache."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CACHE_SEC = 45.0
LOOKUP_TIMEOUT_SEC = 4.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "data": None}
# Last observed egress IP while Amnezia full-tunnel was NOT active.
_base_ip_when_direct: str | None = None
_state_path = Path(__file__).resolve().parent.parent / "logs" / "public_ip_state.json"


def _load_persisted_base() -> str | None:
    try:
        if _state_path.is_file():
            data = json.loads(_state_path.read_text(encoding="utf-8"))
            ip = data.get("base_ip")
            if isinstance(ip, str) and ip.count(".") == 3:
                return ip
    except Exception:
        pass
    return None


def _persist_base(ip: str) -> None:
    try:
        _state_path.parent.mkdir(parents=True, exist_ok=True)
        _state_path.write_text(
            json.dumps({"base_ip": ip, "updated_at": time.time()}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


_base_ip_when_direct = _load_persisted_base()


def amnezia_tunnel_active() -> bool:
    """True only when an AmneziaWGTunnel* service is Running (not just GUI)."""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Service -Name 'AmneziaWGTunnel*' -ErrorAction SilentlyContinue |"
                " Where-Object Status -eq 'Running' | Measure-Object).Count",
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
            encoding="utf-8",
            errors="replace",
        )
        return int((out.stdout or "0").strip() or "0") > 0
    except Exception:
        return False


def chatgpt_proxy_listening(port: int = 10808) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False


def _fetch_ip_payload() -> dict[str, Any]:
    """Query a public geo-IP API. Raises on network failure."""
    url = "https://ipapi.co/json/"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GPUProfiler/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT_SEC) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("unexpected IP API payload")
    ip = data.get("ip")
    if not ip:
        err = data.get("error") or data.get("reason") or "no ip"
        raise ValueError(str(err))
    org = data.get("org") or data.get("asn") or ""
    return {
        "ip": str(ip),
        "country": data.get("country_name") or data.get("country") or "",
        "city": data.get("city") or "",
        "region": data.get("region") or "",
        "org": str(org),
        "asn": str(data.get("asn") or ""),
    }


def _lookup_uncached() -> dict[str, Any]:
    global _base_ip_when_direct

    amnezia_on = amnezia_tunnel_active()
    proxy_on = chatgpt_proxy_listening()

    error: str | None = None
    geo: dict[str, Any] | None = None
    try:
        geo = _fetch_ip_payload()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        error = f"lookup failed: {exc}"

    current_ip = (geo or {}).get("ip") if geo else None

    if current_ip and not amnezia_on:
        _base_ip_when_direct = current_ip
        _persist_base(current_ip)

    base_ip = _base_ip_when_direct
    if amnezia_on and not base_ip:
        base_note = "unknown while full VPN is active"
        base_ip_display: str | None = None
    else:
        base_note = None
        base_ip_display = base_ip

    if amnezia_on:
        mode = "full_vpn"
        mode_label = "Full VPN likely active (Amnezia)"
        if current_ip and base_ip and current_ip != base_ip:
            vpn_status = "PUBLIC IP CHANGED"
        elif current_ip and base_ip and current_ip == base_ip:
            vpn_status = "PUBLIC IP NOT CHANGED (unexpected for full VPN)"
        elif current_ip and not base_ip:
            vpn_status = "PUBLIC IP CHANGED (base unknown)"
        else:
            vpn_status = "Amnezia tunnel up · public IP lookup failed"
    elif proxy_on:
        mode = "selective_proxy"
        mode_label = "Selective proxy active (ChatGPT PAC)"
        # System egress IP usually unchanged; browser ChatGPT may differ.
        if current_ip and base_ip and current_ip != base_ip:
            vpn_status = "PUBLIC IP CHANGED (system egress)"
        else:
            vpn_status = "PUBLIC IP NOT CHANGED (system; browser PAC may differ)"
    else:
        mode = "direct"
        mode_label = "Direct connection"
        if current_ip and base_ip and current_ip != base_ip:
            vpn_status = "PUBLIC IP CHANGED"
        else:
            vpn_status = "PUBLIC IP NOT CHANGED"

    location_parts = [p for p in [(geo or {}).get("city"), (geo or {}).get("country")] if p]
    location = ", ".join(location_parts) if location_parts else "—"

    return {
        "ok": error is None,
        "error": error,
        "current_ip": current_ip or "—",
        "base_ip": base_ip_display,
        "base_ip_note": base_note,
        "country": (geo or {}).get("country") or "—",
        "city": (geo or {}).get("city") or "—",
        "location": location,
        "org": (geo or {}).get("org") or "—",
        "asn": (geo or {}).get("asn") or "",
        "amnezia_on": amnezia_on,
        "proxy_on": proxy_on,
        "mode": mode,
        "mode_label": mode_label,
        "vpn_status": vpn_status,
        "cached": False,
        "ts": time.time(),
    }


def get_public_ip_status(*, force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _lock:
        cached = _cache.get("data")
        if (
            not force
            and cached
            and now - float(_cache.get("ts") or 0.0) < CACHE_SEC
        ):
            out = dict(cached)
            out["cached"] = True
            # Cheap live flags without re-hitting IP API.
            out["amnezia_on"] = amnezia_tunnel_active()
            out["proxy_on"] = chatgpt_proxy_listening()
            return out
        data = _lookup_uncached()
        _cache["ts"] = now
        _cache["data"] = data
        return dict(data)
