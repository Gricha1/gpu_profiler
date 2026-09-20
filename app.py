"""Live GPU / RAM monitor for lab SSH hosts."""

from __future__ import annotations

import asyncio
import csv
import hmac
import json
import os
import re
import socket
import subprocess
import threading
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cursor_projects import (
    discover_local_projects,
    discover_projects,
    open_local_project,
    open_remote_project,
)
from local_browse import fs_list as local_fs_list
from local_probe import probe_local
from remote_browse import fs_list, fs_repos, fs_roots
import sdk_agent
from host_paths import (
    apply_protected_routes,
    best_ssh_target,
    cached_host_paths,
    load_host_paths,
    probe_host_paths,
    zerotier_networks,
)
from services import proxy_manager
from services.mesh_health import get_mesh_health
from services.mesh_watcher_status import get_watcher_status, start_mesh_watcher
from services.public_ip import get_public_ip_status
from services.quotas import aggregator as quotas_aggregator
import user_tracking
import gpu_metrics_history
import ssh_runtime
import user_config

HOSTS = list(load_host_paths().keys())

SSH_TIMEOUT_SEC = 8
SSH_JUMP_TIMEOUT_SEC = 14
REFRESH_CACHE_SEC = 4

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
USERS_DB = ROOT / "data" / "users.sqlite3"
LAST_GOOD_DIR = ROOT / "data" / "metrics" / "last_good"
VPN_DIR = Path.home() / ".ssh" / "timeweb-vpn"
VPN_BYPASS_SCRIPT = VPN_DIR / "amnezia-bypass-routes.ps1"
VPN_SERVICE = "AmneziaWGTunnel$pcawg"
VPN_AWG_PORT = 32457
# Canonical PID-safe scripts live in-repo; ~/.ssh/timeweb-vpn wrappers also call these.
VPN_SSH_START = ROOT / "scripts" / "proxy" / "start_chatgpt_proxy.ps1"
VPN_SSH_STOP = ROOT / "scripts" / "proxy" / "stop_chatgpt_proxy.ps1"
VPN_FULL_START = VPN_DIR / "start-vpn-full.ps1"
VPN_AMNEZIA_START = VPN_DIR / "start-amnezia.ps1"
VPN_AMNEZIA_STOP = VPN_DIR / "stop-amnezia.ps1"
NETBIRD_EXE = Path(r"C:\Program Files\Netbird\netbird.exe")


def _load_vps_meta() -> dict[str, Any]:
    path = VPN_DIR / "vps.json"
    defaults = {
        "ip": "147.45.227.10",
        "hostname": "ams-1-vm-muad",
        "label": "Timeweb AMS-1",
        "region": "Amsterdam",
        "ssh_host": "timeweb-vps",
        "ssh_user": "root",
    }
    try:
        import json

        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update({k: v for k, v in data.items() if v is not None})
    except Exception:
        pass
    return defaults


VPN_META = _load_vps_meta()
VPN_VPS_IP = str(VPN_META.get("ip") or "147.45.227.10")
VPN_SSH_HOST = str(VPN_META.get("ssh_host") or "timeweb-vps")
VPN_VPS_LABEL = str(VPN_META.get("label") or VPN_VPS_IP)
VPN_VPS_HOSTNAME = str(VPN_META.get("hostname") or "")
VPN_VPS_REGION = str(VPN_META.get("region") or "")


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE from .env into os.environ (does not override existing)."""
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


_load_dotenv()
user_config.configure(USERS_DB)
user_config.initialize(load_host_paths())
HOSTS[:] = list(user_config.all_hosts())

REMOTE_REFRESH_SEC = max(5.0, float(os.getenv("GPU_MONITOR_REFRESH_SEC", "15")))
REMOTE_BACKOFF_MAX_SEC = max(
    REMOTE_REFRESH_SEC, float(os.getenv("GPU_MONITOR_BACKOFF_MAX_SEC", "120"))
)
ssh_runtime.configure(int(os.getenv("GPU_MONITOR_SSH_MAX_ACTIVE", "3")))


def _probe_script() -> bytes:
    return (ROOT / "remote_probe.py").read_bytes()


def _last_good_path(host: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", host)
    return LAST_GOOD_DIR / f"{safe}.json"


def _persist_last_good(host: str, state: dict[str, Any]) -> None:
    """Atomically persist a successful snapshot for restart continuity."""
    LAST_GOOD_DIR.mkdir(parents=True, exist_ok=True)
    path = _last_good_path(host)
    temporary = path.with_suffix(".json.tmp")
    payload = {**state, "persisted_at": time.time()}
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_last_good_cache() -> None:
    """Restore last-known metrics without claiming the host is currently up."""
    now = time.time()
    for host in list(HOSTS):
        path = _last_good_path(host)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("host") != host or not data.get("ok"):
            continue
        last_success = float(data.get("last_success_at") or data.get("persisted_at") or 0.0)
        _host_cache[host] = {
            **data,
            "ok": True,
            "connection_ok": False,
            "reachable": False,
            "polling": False,
            "stale": True,
            "error": "обновление после запуска…",
            "last_success_at": last_success or None,
            "last_attempt_at": now,
        }
        _cache["ts"] = max(float(_cache.get("ts") or 0.0), last_success)


_cache: dict[str, Any] = {
    "ts": 0.0, "local": None, "local_ts": 0.0, "gen": 0,
    "zerotier": [], "zerotier_ts": 0.0,
}
_refreshing = False
_local_refreshing = False
_zerotier_refreshing = False
LOCAL_REFRESH_SEC = 2.0
_cache_gen = 0

logger = logging.getLogger("gpu_monitor")

# Per-host independentный кэш — каждый хост обновляется независимо
_host_cache: dict[str, dict[str, Any]] = {}
_probe_sem = asyncio.Semaphore(3)
_host_generation: dict[str, int] = {host: 0 for host in HOSTS}
_host_tasks: dict[str, asyncio.Task[Any]] = {}
_host_next_due: dict[str, float] = {host: 0.0 for host in HOSTS}
_host_failures: dict[str, int] = {}
_background_tasks: set[asyncio.Task[Any]] = set()
_scheduler_stop: asyncio.Event | None = None
_config_lock: asyncio.Lock | None = None
_lifecycle_started = False


def _current_metrics() -> dict[str, Any]:
    """Return current cached metrics data."""
    servers = [_host_cache[h] for h in HOSTS if h in _host_cache]
    return {
        "updated_at": _cache["ts"],
        "servers": _with_local(servers),
    }


def _host_cache_paths(host: str) -> list[dict[str, Any]]:
    path_cfg = load_host_paths()
    return [
        {
            "id": p.get("id"),
            "label": p.get("label"),
            "kind": p.get("kind"),
            "protected": bool(p.get("protected")),
            "ok": False,
            "ms": None,
            "detail": "probing…",
            "ssh_target": p.get("ssh_target"),
            "ip": p.get("ip"),
            "port": p.get("port"),
            "via": p.get("via"),
            "host_name": p.get("host"),
        }
        for p in (path_cfg.get(host) or [])
    ]


def _placeholder_servers() -> list[dict[str, Any]]:
    path_cfg = load_host_paths()
    out: list[dict[str, Any]] = []
    for h in HOSTS:
        stubs = [
            {
                "id": p.get("id"),
                "label": p.get("label"),
                "kind": p.get("kind"),
                "protected": bool(p.get("protected")),
                "ok": False,
                "ms": None,
                "detail": "probing…",
                "ip": p.get("ip"),
                "port": p.get("port"),
                "via": p.get("via"),
                "host_name": p.get("host"),
            }
            for p in (path_cfg.get(h) or [])
        ]
        out.append(
            {
                "host": h,
                "ok": False,
                "reachable": False,
                "error": "загрузка…",
                "gpus": [],
                "ram": None,
                "latency_ms": None,
                "paths": stubs,
            }
        )
    return out


def _with_local(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    local = _cache.get("local")
    if not local:
        local = {
            "host": "local",
            "label": "этот ПК",
            "local": True,
            "ok": False,
            "error": "загрузка…",
            "gpus": [],
            "ram": None,
            "ram_top": [],
            "latency_ms": None,
        }
    return [local, *servers]

@asynccontextmanager
async def _lifespan(_application: FastAPI):
    await asyncio.to_thread(_write_host_paths_atomic, user_config.all_hosts())
    await _startup_probe_local()
    try:
        yield
    finally:
        await _shutdown_sdk()


app = FastAPI(title="GPU Monitor", lifespan=_lifespan)


def _track_task(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def require_admin(request: Request) -> None:
    """Allow local administration, or require the configured bearer token."""
    if request.client and request.client.host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return
    token = os.getenv("GPU_MONITOR_ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("x-admin-token", "")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if token:
        if supplied and hmac.compare_digest(supplied, token):
            return
    raise HTTPException(status_code=403, detail="administrator access required")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _current_user(request: Request) -> dict[str, Any]:
    user = user_config.user_for_ip(_client_ip(request))
    if not user:
        raise HTTPException(status_code=401, detail="select user")
    return user


def _can_delete_host(user: dict[str, Any], host: dict[str, Any] | None) -> bool:
    return bool(host and (user["is_admin"] or (
        host["visibility"] == "private"
        and str(host["owner"]).casefold() == str(user["username"]).casefold()
    )))


class _LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)


@app.get("/api/session")
async def api_session(request: Request) -> dict[str, Any]:
    user = await asyncio.to_thread(user_config.user_for_ip, _client_ip(request))
    return {"ok": True, "user": user}


@app.post("/api/session")
async def api_select_user(body: _LoginBody, request: Request) -> dict[str, Any]:
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9_.-]+", username):
        raise HTTPException(status_code=400, detail="invalid username")
    is_admin = username.casefold() == "admin"
    admin_password = os.getenv("GPU_MONITOR_ADMIN_PASSWORD", "0000")
    if is_admin and not hmac.compare_digest(body.password, admin_password):
        raise HTTPException(status_code=403, detail="неверный пароль администратора")
    user = await asyncio.to_thread(
        user_config.bind_user, _client_ip(request), "admin" if is_admin else username,
        is_admin=is_admin,
    )
    return {"ok": True, "user": user}


@app.middleware("http")
async def _no_cache_html(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")

_vpn_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_vpn_status_lock = threading.Lock()
_ssh_cache: dict[str, Any] = {"ts": 0.0, "ok": None}
VPN_CACHE_SEC = 10.0
SSH_CACHE_SEC = 30.0
_pac_ensure_ts = 0.0


def _basename(path: str) -> str:
    path = path.strip()
    if not path:
        return "?"
    return path.rstrip("/").split("/")[-1] or path


def _run_ps(command: str, timeout: int = 20) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return (proc.stdout or proc.stderr or "").strip()


def _public_ip(timeout_sec: int = 4) -> str:
    return _run_ps(
        f"curl.exe -4 -s --max-time {timeout_sec} https://ifconfig.me",
        timeout=timeout_sec + 4,
    ).strip()


def _vpn_speed_mb_s(timeout_sec: int = 6) -> float | None:
    out = _run_ps(
        f"$t=Measure-Command {{ curl.exe -4 -s --max-time {timeout_sec} -o NUL "
        '"https://speed.cloudflare.com/__down?bytes=1000000" 2>$null }}; '
        "[math]::Round(1 / [math]::Max($t.TotalSeconds, 0.001), 2)",
        timeout=timeout_sec + 4,
    )
    try:
        return float(out.replace(",", "."))
    except ValueError:
        return None


def _tcp_connect_ms(host: str, port: int, timeout: float = 4.0) -> float | None:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.perf_counter() - t0) * 1000
            return round(ms, 1) if ms > 0 else None
    except OSError:
        return None


def _parse_ping_ms(out: str) -> float | None:
    for pattern in (
        r"(?:Average|Среднее)\s*=\s*(\d+)\s*(?:ms|мс)",
        r"(?:time|время)[=<](\d+)\s*(?:ms|мс)?",
        r"(\d+)\s*(?:ms|мс)\s*TTL",
    ):
        m = re.search(pattern, out, re.I)
        if m:
            return float(m.group(1))
    return None


def _vps_latency_ms(vpn_running: bool = False) -> float | None:
    """Measure path to VPS: TCP first (fast), ICMP optional."""
    if vpn_running:
        for port in (22, 443, 80):
            tcp = _tcp_connect_ms(VPN_VPS_IP, port, timeout=2.0)
            if tcp is not None:
                return tcp
        return None
    tcp = _tcp_connect_ms("bore.pub", 40906, timeout=2.5)
    if tcp is not None:
        return tcp
    try:
        out = subprocess.run(
            ["ping", "-n", "1", "-w", "1200", VPN_VPS_IP],
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        return _parse_ping_ms((out.stdout or "") + (out.stderr or ""))
    except Exception:
        return None


def _amnezia_tun2_up() -> bool:
    """True when Amnezia Xray/tun2socks path is active (tun2 Up)."""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "@(Get-NetAdapter -Name 'tun2' -ErrorAction SilentlyContinue |"
                " Where-Object { $_.Status -eq 'Up' }).Count",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        return int((out.stdout or "0").strip().splitlines()[-1] or "0") > 0
    except Exception:
        return False


def _vpn_service_state() -> str:
    """Amnezia connected: AmneziaWG tunnel service OR tun2 (Xray/tun2socks).

    Do NOT treat AmneziaVPN.exe alone as connected — the GUI can sit idle.
    """
    # Current Amnezia Full VPN on this machine uses tun2socks → tun2
    if _amnezia_tun2_up():
        return "Running"

    # Legacy / alternate: AmneziaWGTunnel*
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Service -Name 'AmneziaWGTunnel*' -ErrorAction SilentlyContinue |"
                " Where-Object { $_.Status -eq 'Running' } |"
                " Select-Object -First 1 -ExpandProperty Name)",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        name = (out.stdout or "").strip()
        if name:
            return "Running"
    except Exception:
        pass
    # Fallback: known legacy tunnel name
    try:
        out = subprocess.run(
            ["sc", "query", VPN_SERVICE],
            capture_output=True,
            text=True,
            timeout=1.5,
            encoding="utf-8",
            errors="replace",
        )
        text = (out.stdout or "") + (out.stderr or "")
        if re.search(r"STATE\s*:\s*\d+\s+RUNNING", text, re.I):
            return "Running"
    except Exception:
        pass
    return "Stopped"


def _vpn_summary(
    speed_mb_s: float | None,
    ping_ms: float | None,
    service_running: bool,
    vps_reachable: bool,
) -> tuple[str, str, bool]:
    if not service_running:
        return ("выключен", "VPN выключен", False)
    if not vps_reachable:
        return ("dead", "VPS не отвечает", True)
    if speed_mb_s is None and ping_ms is None:
        return ("unknown", "нет данных", False)
    if (speed_mb_s is not None and speed_mb_s < 1.0) or (ping_ms is not None and ping_ms > 220):
        return ("slow", "медленно", True)
    if (speed_mb_s is not None and speed_mb_s < 2.0) or (ping_ms is not None and ping_ms > 150):
        return ("ok", "средне", False)
    return ("fast", "быстро", False)


def _ssh_probe_host(target: str, timeout: int = 5) -> tuple[bool, float | None]:
    """Return (ok, round_trip_ms) for a single ssh target spec."""
    t0 = time.perf_counter()
    try:
        with ssh_runtime.slot(target, "vpn"):
            out = _run_ps(
                f"ssh -o ConnectTimeout={timeout} -o BatchMode=yes {target} echo OK 2>&1",
                timeout=timeout + 4,
            )
        ms = round((time.perf_counter() - t0) * 1000, 0)
        return ("OK" in out, ms if "OK" in out else None)
    except Exception:
        return (False, None)


def _probe_ssh_paths() -> dict[str, Any]:
    key = str(Path.home() / ".ssh" / "id_ed25519").replace("\\", "/")
    # Fast path: configured Host alias (current VPS, direct)
    host_ok, host_ms = _ssh_probe_host(VPN_SSH_HOST, timeout=4)
    direct_ok, direct_ms = (False, None)
    if not host_ok:
        direct_ok, direct_ms = _ssh_probe_host(
            f"-o IdentitiesOnly=yes -i {key} root@{VPN_VPS_IP}",
            timeout=4,
        )
    if host_ok or direct_ok:
        ms = host_ms if host_ok else direct_ms
        path, label = "direct", f"есть · прямой · {int(ms or 0)} ms"
        access = "есть"
    else:
        path, label = "none", "нет SSH"
        access = "нет"
    return {
        "ssh_direct_ok": bool(host_ok or direct_ok),
        "ssh_direct_ms": host_ms if host_ok else direct_ms,
        "ssh_bore_ok": False,
        "ssh_bore_ms": None,
        "ssh_path": path,
        "ssh_path_label": label,
        "ssh_available": bool(host_ok or direct_ok),
        "ssh_access_label": access,
    }


def _probe_ssh_paths_cached() -> dict[str, Any]:
    now = time.time()
    cached = _ssh_cache.get("paths")
    ts = float(_ssh_cache.get("paths_ts") or 0.0)
    if cached and now - ts < SSH_CACHE_SEC:
        return cached
    data = _probe_ssh_paths()
    _ssh_cache["paths"] = data
    _ssh_cache["paths_ts"] = now
    return data


def _ssh_available(vpn_running: bool = False) -> bool:
    if vpn_running:
        ok, _ = _ssh_probe_host(
            f"-o StrictHostKeyChecking=accept-new -i $env:USERPROFILE\\.ssh\\id_ed25519 root@{VPN_VPS_IP}",
            timeout=5,
        )
        return ok
    return bool(_probe_ssh_paths_cached().get("ssh_available"))


def _ssh_available_cached(vpn_running: bool = False) -> bool:
    if vpn_running:
        now = time.time()
        cache_key = "ok_vpn"
        ts_key = "ts_vpn"
        cached = _ssh_cache.get(cache_key)
        ts = float(_ssh_cache.get(ts_key) or 0.0)
        if cached is not None and now - ts < SSH_CACHE_SEC:
            return bool(cached)
        ok = _ssh_available(vpn_running=True)
        _ssh_cache[ts_key] = now
        _ssh_cache[cache_key] = ok
        return ok
    return bool(_probe_ssh_paths_cached().get("ssh_available"))


def _tcp_listening(port: int, host: str = "127.0.0.1") -> bool:
    """Fast local listen check — avoids slow Get-NetTCPConnection."""
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _process_running(name: str) -> bool:
    # Prefer psutil-free WinAPI snapshot; fall back to tasklist.
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == INVALID_HANDLE_VALUE:
            raise OSError("snapshot failed")
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            target = name.lower()
            if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                while True:
                    if entry.szExeFile.lower() == target:
                        return True
                    if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snap)
        return False
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            timeout=2,
            encoding="utf-8",
            errors="replace",
        )
        return name.lower() in (out.stdout or "").lower()
    except Exception:
        return False


def _pac_chatgpt_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            url, _ = winreg.QueryValueEx(key, "AutoConfigURL")
        if isinstance(url, str) and "chatgpt.pac" in url.lower():
            return True
    except OSError:
        pass
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Policies\Google\Chrome",
        ) as key:
            url, _ = winreg.QueryValueEx(key, "ProxyPacUrl")
        if isinstance(url, str) and "chatgpt.pac" in url.lower():
            return True
    except OSError:
        pass
    return False


def _ssh_vpn_running() -> bool:
    """Browser proxy VPN (sing-box mixed on 10808), not full TUN."""
    return _proxy_vpn_running()


def _full_vpn_running() -> bool:
    """Full system TUN via SSH (twvpn0)."""
    try:
        out = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True,
            text=True,
            timeout=2.0,
            encoding="utf-8",
            errors="replace",
        )
        if "twvpn0" in (out.stdout or "").lower():
            return True
    except Exception:
        pass
    # Fallback: mark from start-vpn-full.ps1 pid file (SS TUN may omit :11080)
    try:
        pid_file = VPN_DIR / "full-vpn.pid"
        if pid_file.is_file():
            pid = int((pid_file.read_text(encoding="utf-8") or "0").strip() or "0")
            if pid > 0 and _process_running("sing-box.exe"):
                return True
    except Exception:
        pass
    return False


def _proxy_ports_up() -> bool:
    # Direct SS to VPS only needs local mixed :10808 (SSH -L :14000 optional)
    return _tcp_listening(10808)


def _proxy_vpn_running() -> bool:
    """ChatGPT PAC proxy: local mixed :10808 (SS -> VPS) + PAC policies."""
    if not _proxy_ports_up():
        return False
    # Full TUN also may listen on 10808/11080 — prefer twvpn0 / full-vpn.pid as Full
    if _full_vpn_running():
        return False
    return True


def _full_vpn_start_now() -> tuple[bool, str]:
    # AmneziaWG conflicts with TUN; do not wait forever on stop
    try:
        _vpn_stop_now()
    except Exception:
        pass
    script_path = str(VPN_FULL_START)
    out = _run_ps(
        f"powershell -NoProfile -ExecutionPolicy Bypass -File '{script_path}'",
        timeout=60,
    )
    return (_full_vpn_running(), out)


def _ensure_pac_http_server() -> None:
    """PAC URL is useless if nothing serves :18080 — browsers then go DIRECT."""
    if _tcp_listening(18080):
        return
    script = VPN_DIR / "chatgpt-pac-server.ps1"
    if not script.is_file():
        return
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script),
            ],
            cwd=str(VPN_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


PAC_FILE = VPN_DIR / "chatgpt.pac"
# Serve PAC from the monitor itself — :18080 PowerShell listener dies silently
# and then browsers fall through to DIRECT (MIPT IP → ChatGPT blocked).
PAC_URL_MONITOR = "http://127.0.0.1:8765/chatgpt.pac"
PAC_URL_FALLBACK = "http://127.0.0.1:18080/chatgpt.pac"


def _clear_chatgpt_pac() -> None:
    """Remove sticky proxy (policies + Preferences) so OFF never leaves dead 10808."""
    lib = VPN_DIR / "chatgpt-proxy-lib.ps1"
    marker = VPN_DIR / "chatgpt-proxy.on"
    if lib.is_file():
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f". '{lib}'; Set-ChatGptYandexProxyOff",
                ],
                capture_output=True,
                text=True,
                timeout=12,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            pass
    try:
        marker.unlink(missing_ok=True)
    except Exception:
        pass
    # Fallback registry wipe if PS failed
    try:
        import winreg
        import json

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            for name in ("AutoConfigURL", "ProxyServer"):
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
        direct = json.dumps({"mode": "direct"}, separators=(",", ":"))
        for pol in (
            r"Software\Policies\Google\Chrome",
            r"Software\Policies\Microsoft\Edge",
            r"Software\Policies\YandexBrowser",
            r"Software\Policies\Yandex\YandexBrowser",
        ):
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, pol, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "ProxyMode", 0, winreg.REG_SZ, "direct")
                winreg.SetValueEx(key, "ProxySettings", 0, winreg.REG_SZ, direct)
                for name in ("ProxyPacUrl", "ProxyServer", "ProxyBypassList"):
                    try:
                        winreg.DeleteValue(key, name)
                    except OSError:
                        pass
        try:
            import ctypes

            wininet = ctypes.windll.wininet
            wininet.InternetSetOptionW(None, 39, None, 0)
            wininet.InternetSetOptionW(None, 37, None, 0)
        except Exception:
            pass
    except Exception:
        pass


def _ensure_chatgpt_pac() -> None:
    """Keep PAC split-tunnel policies while tunnel is up (never fixed_servers)."""
    _ensure_pac_http_server()
    pac_url = PAC_URL_MONITOR if _tcp_listening(8765) else PAC_URL_FALLBACK
    try:
        import winreg
        import json

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, pac_url)
            try:
                winreg.DeleteValue(key, "ProxyServer")
            except OSError:
                pass
        proxy_settings = json.dumps({"mode": "pac_script", "pacUrl": pac_url}, separators=(",", ":"))
        for pol in (
            r"Software\Policies\Google\Chrome",
            r"Software\Policies\Microsoft\Edge",
            r"Software\Policies\YandexBrowser",
            r"Software\Policies\Yandex\YandexBrowser",
        ):
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, pol, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "QuicAllowed", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "ProxyMode", 0, winreg.REG_SZ, "pac_script")
                winreg.SetValueEx(key, "ProxyPacUrl", 0, winreg.REG_SZ, pac_url)
                winreg.SetValueEx(key, "ProxySettings", 0, winreg.REG_SZ, proxy_settings)
                try:
                    winreg.DeleteValue(key, "ProxyServer")
                except OSError:
                    pass
        try:
            (VPN_DIR / "chatgpt-proxy.on").write_text(f"pac={pac_url}", encoding="utf-8")
        except Exception:
            pass
        try:
            import ctypes

            wininet = ctypes.windll.wininet
            wininet.InternetSetOptionW(None, 39, None, 0)
            wininet.InternetSetOptionW(None, 37, None, 0)
        except Exception:
            pass
    except Exception:
        pass


def _open_chatgpt_yandex() -> None:
    """Open ChatGPT tab in Yandex with PAC proxy (does not close existing windows)."""
    script = VPN_DIR / "open-chatgpt-yandex.ps1"
    if not script.is_file():
        return
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script),
            ],
            cwd=str(VPN_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _full_vpn_stop_now() -> tuple[bool, str]:
    script = VPN_DIR / "stop-vpn.ps1"
    out = ""
    if script.is_file():
        out = _run_ps(
            f"powershell -NoProfile -ExecutionPolicy Bypass -File '{script}'",
            timeout=20,
        )
    else:
        out = _ssh_vpn_stop_now()[1]
    still = _full_vpn_running()
    return (not still, out or ("FULL VPN OFF" if not still else "still running"))


def _ssh_vpn_start_now() -> tuple[bool, str, bool]:
    """Start ChatGPT PAC proxy via PID-safe in-repo scripts. Returns (ok, message, already_up).

    Cold start is fire-and-forget so the UI unlocks in <1s; tunnel may still
    come up for a few seconds via bore.
    """
    if _proxy_ports_up():
        _ensure_chatgpt_pac()
        if not _tcp_listening(18080):
            try:
                subprocess.Popen(
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-WindowStyle",
                        "Hidden",
                        "-File",
                        str(VPN_DIR / "chatgpt-pac-server.ps1"),
                    ],
                    cwd=str(VPN_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
        _open_chatgpt_yandex()
        return (True, "ChatGPT PROXY ON (already running)", True)

    script_path = str(VPN_SSH_START)
    if not Path(script_path).is_file():
        return (False, f"missing script: {script_path}", False)
    try:
        # Don't wait for bore/SSH — return immediately and let status poll catch up.
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                script_path,
            ],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return (False, f"start failed: {exc}", False)
    _ensure_chatgpt_pac()
    _open_chatgpt_yandex()
    return (True, "ChatGPT PROXY starting…", False)


def _ssh_vpn_stop_now() -> tuple[bool, str]:
    script_path = str(VPN_SSH_STOP)
    out = _run_ps(
        f"powershell -NoProfile -ExecutionPolicy Bypass -File '{script_path}'",
        timeout=30,
    )
    _clear_chatgpt_pac()
    return (not _proxy_vpn_running(), out)


def _awg_udp_probe() -> tuple[bool, str]:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.5)
        sock.sendto(b"\x00", (VPN_VPS_IP, VPN_AWG_PORT))
        try:
            sock.recvfrom(256)
            return True, f"UDP {VPN_AWG_PORT} ответил"
        except socket.timeout:
            return True, f"UDP {VPN_AWG_PORT} доступен (без ответа — норма для AWG)"
    except OSError as exc:
        return False, f"UDP {VPN_AWG_PORT}: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _ssh_run(cmd: str, timeout: int = 25) -> str:
    safe = cmd.replace("'", "'\"'\"'")
    with ssh_runtime.slot("timeweb-vps", "vpn"):
        return _run_ps(
            f"ssh -o ConnectTimeout=6 -o BatchMode=yes timeweb-vps '{safe}' 2>&1",
            timeout=timeout,
        )


def _vpn_preflight() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    ssh_ok = "OK" in _ssh_run("echo OK", timeout=10)
    checks.append({
        "id": "ssh",
        "label": "SSH (bore)",
        "ok": ssh_ok,
        "detail": "доступен" if ssh_ok else "недоступен",
    })

    yt_ok = False
    yt_detail = "не проверено"
    gg_ok = False
    gg_detail = "не проверено"
    vps_ip = ""
    vps_speed = None

    if ssh_ok:
        remote = _ssh_run(
            "yt=$(curl -4 -s -o /dev/null -w '%{http_code}:%{time_total}' --max-time 8 "
            "https://www.youtube.com 2>/dev/null || echo fail); "
            "gg=$(curl -4 -s -o /dev/null -w '%{http_code}:%{time_total}' --max-time 8 "
            "https://www.google.com 2>/dev/null || echo fail); "
            "ip=$(curl -4 -s --max-time 5 https://ifconfig.me/ip 2>/dev/null || echo); "
            "spd=$(curl -4 -s -o /dev/null -w '%{speed_download}' --max-time 10 "
            "'https://speed.cloudflare.com/__down?bytes=500000' 2>/dev/null || echo 0); "
            "echo YT=$yt; echo GG=$gg; echo IP=$ip; echo SPD=$spd",
            timeout=35,
        )
        for line in remote.splitlines():
            line = line.strip()
            if line.startswith("YT="):
                parts = line[3:].split(":")
                if len(parts) >= 2 and parts[0].isdigit():
                    code, sec = parts[0], parts[1]
                    yt_ok = code in ("200", "301", "302")
                    yt_detail = f"HTTP {code}, {float(sec):.2f}s с VPS"
                else:
                    yt_detail = "нет ответа с VPS"
            elif line.startswith("GG="):
                parts = line[3:].split(":")
                if len(parts) >= 2 and parts[0].isdigit():
                    code, sec = parts[0], parts[1]
                    gg_ok = code in ("200", "301", "302")
                    gg_detail = f"HTTP {code}, {float(sec):.2f}s с VPS"
                else:
                    gg_detail = "нет ответа с VPS"
            elif line.startswith("IP="):
                vps_ip = line[3:].strip()
            elif line.startswith("SPD="):
                try:
                    vps_speed = float(line[4:]) / (1024 * 1024)
                except ValueError:
                    vps_speed = None

    checks.append({
        "id": "vps_youtube",
        "label": "YouTube с VPS",
        "ok": yt_ok,
        "detail": yt_detail,
    })
    checks.append({
        "id": "vps_google",
        "label": "Google с VPS",
        "ok": gg_ok,
        "detail": gg_detail,
    })
    checks.append({
        "id": "vps_ip",
        "label": "IP VPS",
        "ok": vps_ip == VPN_VPS_IP,
        "detail": vps_ip or "—",
    })
    if vps_speed is not None:
        spd_ok = vps_speed >= 0.3
        checks.append({
            "id": "vps_speed",
            "label": "Скорость VPS",
            "ok": spd_ok,
            "detail": f"{vps_speed:.2f} MB/s",
        })
        if not spd_ok:
            warnings.append("низкая скорость на VPS")

    awg_ok, awg_detail = _awg_udp_probe()
    checks.append({
        "id": "awg_udp",
        "label": "AWG endpoint",
        "ok": awg_ok,
        "detail": awg_detail,
    })
    if not awg_ok:
        warnings.append("AWG UDP с ПК не доступен — туннель может не подняться")

    latency = _vps_latency_ms(vpn_running=False)
    checks.append({
        "id": "vps_latency",
        "label": "Задержка до VPS",
        "ok": latency is not None and latency < 400,
        "detail": f"{int(latency)} ms" if latency is not None else "нет ответа",
    })

    critical_ok = ssh_ok and yt_ok and gg_ok
    safe = critical_ok and awg_ok and (latency is None or latency < 400)

    if not ssh_ok:
        message = "SSH недоступен — VPN включать нельзя"
    elif not yt_ok:
        message = "YouTube с VPS не открывается — VPN включать нельзя"
    elif not gg_ok:
        message = "Google с VPS не открывается — VPN включать нельзя"
    elif not awg_ok:
        message = "AWG endpoint недоступен — VPN скорее всего не заработает"
        safe = False
    elif warnings:
        message = "VPS OK, но есть предупреждения"
    else:
        message = "Проверка пройдена — можно включать VPN"

    return {
        "safe_to_enable": safe,
        "critical_ok": critical_ok,
        "checks": checks,
        "warnings": warnings,
        "message": message,
    }


def _vpn_postcheck() -> dict[str, Any]:
    time.sleep(4)
    public_ip = _public_ip(timeout_sec=10)
    yt_code = _run_ps(
        'curl.exe -4 -s -o NUL -w "%{http_code}" --max-time 12 https://www.youtube.com',
        timeout=15,
    ).strip()
    ip_ok = public_ip == VPN_VPS_IP
    yt_ok = yt_code in ("200", "301", "302")
    ok = ip_ok and yt_ok
    return {
        "ok": ok,
        "public_ip": public_ip,
        "public_ip_ok": ip_ok,
        "youtube_http": yt_code,
        "youtube_ok": yt_ok,
    }


def _netbird_bin() -> str | None:
    if NETBIRD_EXE.is_file():
        return str(NETBIRD_EXE)
    try:
        out = subprocess.run(
            ["where", "netbird"],
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        line = (out.stdout or "").strip().splitlines()
        return line[0] if line else None
    except Exception:
        return None


def _netbird_status_text() -> str:
    exe = _netbird_bin()
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, "status"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def _netbird_running() -> bool:
    # Fast path: don't shell out to `netbird status` (often 5–8s) on UI poll.
    return _process_running("netbird.exe")


def _netbird_info() -> dict[str, Any]:
    # Never call `netbird status` from the hot status path — it can hang 5–15s.
    on = _process_running("netbird.exe")
    cached = (_vpn_cache.get("data") or {})
    return {
        "on": on,
        "fqdn": cached.get("netbird_fqdn") if on else None,
        "ip": cached.get("netbird_ip") if on else None,
        "peers": cached.get("netbird_peers") if on else None,
    }


def _netbird_start_now() -> tuple[bool, str]:
    exe = _netbird_bin()
    if not exe:
        return (False, "netbird.exe не найден")
    # AmneziaWG conflicts with Netbird; Full VPN is OK (100.98/16 excluded from TUN)
    _vpn_stop_now()
    # Daemon pipe requires Windows service Running
    try:
        subprocess.run(
            ["sc", "start", "NetBird"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        pass
    try:
        out = subprocess.run(
            [exe, "up"],
            capture_output=True,
            text=True,
            timeout=45,
            encoding="utf-8",
            errors="replace",
        )
        msg = ((out.stdout or "") + (out.stderr or "")).strip()
        # Prefer real connection signal over just process presence
        st = _netbird_status_text()
        ok = (
            "Management: Connected" in st
            or "Already connected" in msg
            or "Connected" in msg
            or _process_running("netbird.exe")
        )
        return (ok, msg or st or ("Connected" if ok else "netbird up failed"))
    except Exception as e:
        return (False, str(e))


def _netbird_stop_now() -> tuple[bool, str]:
    exe = _netbird_bin()
    if not exe:
        return (True, "netbird not installed")
    try:
        out = subprocess.run(
            [exe, "down"],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        msg = ((out.stdout or "") + (out.stderr or "")).strip()
        still = _netbird_running()
        return (not still, msg or ("Disconnected" if not still else "still connected"))
    except Exception as e:
        return (False, str(e))


def _collect_vpn_status() -> dict[str, Any]:
    amnezia_on = _vpn_service_state().lower() == "running"
    full_on = _full_vpn_running()
    proxy_on = _proxy_vpn_running()
    nb = _netbird_info()
    netbird_on = bool(nb.get("on"))
    if full_on:
        active_mode = "full"
        active_label = "Full VPN" + (" + Netbird" if netbird_on else "")
    elif proxy_on:
        active_mode = "proxy"
        active_label = "ChatGPT+YT+Comet" + (" + Netbird" if netbird_on else "")
    elif amnezia_on:
        active_mode = "amnezia"
        active_label = "Amnezia"
    elif netbird_on:
        active_mode = "netbird"
        active_label = "Netbird · МФТИ"
    else:
        active_mode = "none"
        active_label = "всё выключено"

    running = amnezia_on  # legacy field for Amnezia metrics
    # Keep status fast: no live SSH / public-IP curl while Full VPN TUN is up.
    ping_ms = None
    vps_reachable = False
    ssh_paths = _ssh_cache.get("paths") or {
        "ssh_available": None,
        "ssh_direct_ok": None,
        "ssh_direct_ms": None,
        "ssh_bore_ok": None,
        "ssh_bore_ms": None,
        "ssh_path": None,
        "ssh_path_label": "—",
        "ssh_access_label": "—",
    }
    if full_on:
        ssh_paths = {
            **ssh_paths,
            "ssh_available": True,
            "ssh_direct_ok": True,
            "ssh_path": "direct",
            "ssh_path_label": "есть · Full VPN up",
            "ssh_access_label": "есть",
        }
        public_ip = VPN_VPS_IP
    else:
        try:
            if not _ssh_cache.get("paths") or (
                time.time() - float(_ssh_cache.get("paths_ts") or 0.0) >= SSH_CACHE_SEC
            ):
                ssh_paths = _probe_ssh_paths_cached()
        except Exception:
            pass
        public_ip = (_vpn_cache.get("data") or {}).get("public_ip") or "—"
    speed_mb_s = None
    quality, quality_label, should_reboot = _vpn_summary(speed_mb_s, ping_ms, amnezia_on, vps_reachable)
    using_vps = public_ip == VPN_VPS_IP
    ssh_ok = bool(ssh_paths.get("ssh_available"))
    vps_display = VPN_VPS_LABEL
    if VPN_VPS_HOSTNAME:
        vps_display = f"{VPN_VPS_LABEL} / {VPN_VPS_HOSTNAME}"
    vps_display_ip = f"{VPN_VPS_IP}" + (f" / {VPN_VPS_REGION}" if VPN_VPS_REGION else "")

    if full_on and using_vps and netbird_on:
        note = f"Full VPN ({VPN_VPS_IP}) + Netbird mesh (100.98) вместе"
    elif full_on and using_vps:
        note = f"Full VPN через {VPN_VPS_LABEL} ({VPN_VPS_IP})"
    elif full_on and ssh_paths.get("ssh_path") == "bore":
        note = "Full VPN через bore (прямой SSH недоступен)"
    elif full_on:
        note = f"Full VPN включён · ожидаемый egress {VPN_VPS_IP}"
    elif proxy_on:
        note = "PAC: ChatGPT/YouTube/Comet через прокси; Public IP выше — системный (не браузер)"
        if netbird_on:
            note += f"; Netbird: {nb.get('fqdn') or nb.get('ip') or 'on'}"
    elif amnezia_on and not vps_reachable:
        note = "Amnezia: VPS не отвечает — нужен ребут"
    elif should_reboot and amnezia_on:
        note = "Amnezia: канал просел — можно ребутнуть VPS"
    elif netbird_on:
        note = f"Netbird МФТИ: {nb.get('fqdn') or 'connected'}" + (
            f" · {nb.get('peers')}" if nb.get("peers") else ""
        )
    elif active_mode == "none":
        note = f"VPN выкл · VPS: {VPN_VPS_IP}"
    else:
        note = f"Активен: {active_label}"

    return {
        "service_state": "Running" if amnezia_on else "Stopped",
        "running": amnezia_on,
        "amnezia_on": amnezia_on,
        "proxy_on": proxy_on,
        "full_on": full_on,
        "netbird_on": netbird_on,
        "netbird_fqdn": nb.get("fqdn"),
        "netbird_ip": nb.get("ip"),
        "netbird_peers": nb.get("peers"),
        "active_mode": active_mode,
        "active_label": active_label,
        "modes": {
            "amnezia": {"on": amnezia_on, "label": "ВКЛ" if amnezia_on else "ВЫКЛ"},
            "proxy": {"on": proxy_on, "label": "ВКЛ" if proxy_on else "ВЫКЛ"},
            "full": {"on": full_on, "label": "ВКЛ" if full_on else "ВЫКЛ"},
            "netbird": {"on": netbird_on, "label": "ВКЛ" if netbird_on else "ВЫКЛ"},
        },
        "vps_reachable": vps_reachable,
        "public_ip": public_ip,
        "using_vps_ip": using_vps,
        "vps_ip": VPN_VPS_IP,
        "vps_label": VPN_VPS_LABEL,
        "vps_hostname": VPN_VPS_HOSTNAME,
        "vps_region": VPN_VPS_REGION,
        "vps_display": vps_display,
        "vps_display_ip": vps_display_ip,
        "speed_mb_s": speed_mb_s,
        "ping_ms": ping_ms,
        "quality": quality,
        "quality_label": quality_label,
        "should_reboot_vps": should_reboot,
        "ssh_available": ssh_ok,
        "ssh_access_label": ssh_paths.get("ssh_access_label") or ("есть" if ssh_ok else "нет"),
        "ssh_direct_ok": ssh_paths.get("ssh_direct_ok"),
        "ssh_direct_ms": ssh_paths.get("ssh_direct_ms"),
        "ssh_bore_ok": ssh_paths.get("ssh_bore_ok"),
        "ssh_bore_ms": ssh_paths.get("ssh_bore_ms"),
        "ssh_path": ssh_paths.get("ssh_path"),
        "ssh_path_label": ssh_paths.get("ssh_path_label"),
        "ssh_vpn_running": proxy_on,
        "full_vpn_running": full_on,
        "note": note,
    }


def _vpn_start_now() -> tuple[bool, str]:
    """Open Amnezia Premium (clears PAC conflicts). User picks Germany in the app."""
    script_path = str(VPN_AMNEZIA_START)
    out = _run_ps(
        f"powershell -NoProfile -ExecutionPolicy Bypass -File '{script_path}'",
        timeout=35,
    )
    app_up = _process_running("AmneziaVPN.exe") or ("Amnezia Premium READY" in out)
    # Tunnel may still be off until user clicks Connect — treat app ready as success.
    return (bool(app_up) or _vpn_service_state().lower() == "running", out)


def _vpn_stop_now() -> tuple[bool, str]:
    """Stop all AmneziaWG tunnels + clear leftover proxy."""
    script_path = str(VPN_AMNEZIA_STOP)
    out = _run_ps(
        f"powershell -NoProfile -ExecutionPolicy Bypass -File '{script_path}'",
        timeout=20,
    )
    running = _vpn_service_state().lower() == "running"
    return (not running, out or ("Stopped" if not running else "Running"))


def _parse_output(host: str, stdout: str, stderr: str, rc: int) -> dict[str, Any]:
    if rc != 0 and not stdout.strip():
        err = (stderr or stdout or f"ssh exit {rc}").strip().splitlines()
        return {
            "host": host,
            "ok": False,
            "error": err[-1] if err else f"ssh exit {rc}",
            "gpus": [],
            "ram": None,
        }

    gpus_raw: list[dict[str, Any]] = []
    uuid_to_idx: dict[str, int] = {}
    procs_by_idx: dict[int, list[dict[str, Any]]] = defaultdict(list)
    pid_to_user: dict[str, str] = {}
    ram = None
    disk = None
    disks: list[dict[str, Any]] = []
    disks_by_mount: dict[str, dict[str, Any]] = {}
    home_disk = None
    all_homes: list[dict[str, Any]] = []
    section = "gpu"

    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---PROCS---":
            section = "procs"
            continue
        if line == "---USERS---":
            section = "users"
            continue
        if line == "---RAM---":
            section = "ram"
            continue
        if line == "---DISK---":
            section = "disk"
            continue
        if line == "---HOME---":
            section = "home"
            continue
        if line == "---ALL_HOMES---":
            section = "all_homes"
            continue

        if section == "gpu":
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            try:
                idx = int(float(parts[0]))
                used = float(parts[2])
                total = float(parts[3])
                util = float(parts[4])
            except ValueError:
                continue
            uuid = parts[5]
            uuid_to_idx[uuid] = idx
            gpus_raw.append(
                {
                    "index": idx,
                    "name": parts[1],
                    "uuid": uuid,
                    "mem_used_mib": used,
                    "mem_total_mib": total,
                    "mem_pct": round(100.0 * used / total, 1) if total else 0.0,
                    "util_pct": util,
                }
            )
        elif section == "procs":
            rows = list(csv.reader([line]))
            if not rows or len(rows[0]) < 3:
                continue
            row = rows[0]
            gpu_uuid = row[0].strip()
            pid = row[1].strip()
            try:
                mem_mib = float(row[2].strip())
            except ValueError:
                mem_mib = 0.0
            proc_name = _basename(",".join(row[3:]).strip() if len(row) > 3 else "")
            idx = uuid_to_idx.get(gpu_uuid)
            if idx is None:
                continue
            procs_by_idx[idx].append(
                {
                    "pid": pid,
                    "mem_mib": mem_mib,
                    "process": proc_name,
                    "user": None,
                }
            )
        elif section == "users":
            if "," not in line:
                continue
            pid, user = line.split(",", 1)
            pid_to_user[pid.strip()] = user.strip() or "?"
        elif section == "ram":
            if not line.startswith("Mem:"):
                continue
            nums = re.findall(r"\d+", line)
            if len(nums) >= 2:
                total_b = int(nums[0])
                used_b = int(nums[1])
                avail_b = int(nums[-1]) if len(nums) >= 6 else max(total_b - used_b, 0)
                ram = {
                    "total_bytes": total_b,
                    "used_bytes": used_b,
                    "available_bytes": avail_b,
                    "used_pct": round(100.0 * used_b / total_b, 1) if total_b else 0.0,
                }
        elif section == "disk":
            if line.startswith("Filesystem") or line.startswith("Файл"):
                continue
            # df -P: Filesystem 1024-blocks Used Available Capacity Mounted
            parts = line.split()
            if len(parts) < 6:
                continue
            try:
                total_b = int(parts[1])
                used_b = int(parts[2])
                avail_b = int(parts[3])
            except ValueError:
                continue
            mount = parts[5]
            entry = {
                "total_bytes": total_b,
                "used_bytes": used_b,
                "available_bytes": avail_b,
                "used_pct": round(100.0 * used_b / total_b, 1) if total_b else 0.0,
                "free_pct": round(100.0 * avail_b / total_b, 1) if total_b else 0.0,
                "mount": mount,
                "filesystem": parts[0],
            }
            # Keep first occurrence order (home mount first), dedupe by mount.
            if mount not in disks_by_mount:
                disks.append(entry)
            disks_by_mount[mount] = entry
            if disk is None:
                disk = entry
        elif section == "home":
            # du -sb: SIZE\tPATH
            parts = line.split("\t", 1)
            if len(parts) < 1:
                continue
            try:
                used_b = int(parts[0].strip().split()[0])
            except ValueError:
                continue
            path = parts[1].strip() if len(parts) > 1 else ""
            home_disk = {
                "used_bytes": used_b,
                "path": path,
            }
        elif section == "all_homes":
            # Format: username\tSIZE\tPATH
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            username = parts[0].strip()
            try:
                used_b = int(parts[1].strip().split()[0])
            except ValueError:
                continue
            path = parts[2].strip() if len(parts) > 2 else ""
            all_homes.append({
                "username": username,
                "used_bytes": used_b,
                "path": path,
            })

    if not disk and disks:
        disk = disks[0]
    if home_disk and disk and disk.get("total_bytes"):
        home_disk["disk_pct"] = round(
            100.0 * home_disk["used_bytes"] / disk["total_bytes"], 2
        )
        home_disk["mount"] = disk.get("mount")
    
    # Calculate disk_pct for all homes
    if disk and disk.get("total_bytes"):
        for h in all_homes:
            h["disk_pct"] = round(
                100.0 * h["used_bytes"] / disk["total_bytes"], 2
            )
        all_homes.sort(key=lambda h: -h["used_bytes"])

    gpus: list[dict[str, Any]] = []
    for g in gpus_raw:
        procs = sorted(
            procs_by_idx.get(g["index"], []),
            key=lambda p: float(p.get("mem_mib") or 0),
            reverse=True,
        )
        for p in procs:
            p["user"] = pid_to_user.get(str(p["pid"]), p.get("user") or "?")
            if not p.get("name"):
                p["name"] = p.get("process") or "?"

        by_user: dict[str, float] = defaultdict(float)
        for p in procs:
            by_user[p["user"] or "?"] += float(p["mem_mib"])
        users = [
            {"user": u, "mem_mib": round(m, 1), "mem_gib": round(m / 1024, 2)}
            for u, m in sorted(by_user.items(), key=lambda kv: -kv[1])
        ]
        gpus.append({**g, "processes": procs, "users": users})

    return {
        "host": host,
        "ok": True,
        "error": None,
        "gpus": gpus,
        "ram": ram,
        "disk": disk,
        "disks": disks,
        "home_disk": home_disk,
        "all_homes": all_homes,
        "gpu_count": len(gpus),
    }


def _ssh_run_metrics(
    target: str,
    extra_opts: list[str],
    connect_timeout: int,
) -> tuple[int, bytes, bytes]:
    """Run remote_probe.py over SSH (thread-safe; avoids Windows asyncio subprocess issues)."""
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "StrictHostKeyChecking=accept-new",
        *extra_opts,
        target,
        "python3",
        "-",
    ]
    try:
        with ssh_runtime.slot(target, "metrics"):
            out = subprocess.run(
                cmd,
                input=_probe_script(),
                capture_output=True,
                timeout=connect_timeout + 25,
                check=False,
            )
        return out.returncode, out.stdout or b"", out.stderr or b""
    except subprocess.TimeoutExpired as exc:
        ssh_runtime.note_result(target, timeout=True)
        return -1, exc.stdout or b"", b"timeout"
    except Exception as exc:  # noqa: BLE001
        return -2, b"", str(exc).encode("utf-8", errors="replace")


async def _probe_host(host: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    path_cfg = load_host_paths().get(host) or []
    prefer = next((p for p in path_cfg if p.get("prefer_for_probe")), None)
    if prefer and prefer.get("kind", "ssh") == "ssh":
        target = str(prefer.get("ssh_target") or host)
        extra_opts = list(prefer.get("ssh_opts") or [])
    else:
        target, extra_opts = host, []

    connect_timeout = (
        SSH_JUMP_TIMEOUT_SEC
        if (
            target == "lab_comp"
            or any("ProxyJump" in str(x) and "none" not in str(x) for x in extra_opts)
        )
        else SSH_TIMEOUT_SEC
    )

    rc, stdout_b, stderr_b = await asyncio.to_thread(
        _ssh_run_metrics, target, extra_opts, connect_timeout
    )

    # Successful regular collection does not fan out over every alternate
    # route.  Full route diagnostics are only run after a metrics failure;
    # otherwise the latest diagnostic snapshot (if any) is reused.
    if rc == 0 and (stdout_b or b"").strip():
        paths = cached_host_paths(host) or _host_cache_paths(host)
        for path in paths:
            if path.get("ssh_target") == target or path.get("id") == (prefer or {}).get("id"):
                path.update({"ok": True, "detail": "metrics route ok"})
    else:
        paths = await asyncio.to_thread(probe_host_paths, host)
    any_path_ok = any(bool(p.get("ok")) for p in paths if p.get("kind") != "anydesk")

    if (rc != 0 or not (stdout_b or b"").strip()) and any_path_ok:
        best_target, best_opts = await asyncio.to_thread(best_ssh_target, host, paths)
        if best_target != target or list(best_opts) != list(extra_opts):
            target, extra_opts = best_target, list(best_opts)
            connect_timeout = (
                SSH_JUMP_TIMEOUT_SEC
                if (
                    target == "lab_comp"
                    or any("ProxyJump" in str(x) and "none" not in str(x) for x in extra_opts)
                )
                else SSH_TIMEOUT_SEC
            )
            rc, stdout_b, stderr_b = await asyncio.to_thread(
                _ssh_run_metrics, target, extra_opts, connect_timeout
            )

    if rc == -1 and not (stdout_b or b"").strip():
        return {
            "host": host,
            "ok": False,
            "reachable": any_path_ok,
            "error": "timeout" if not any_path_ok else "probe timeout (path alive)",
            "gpus": [],
            "ram": None,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "paths": paths,
            "ssh_via": target,
            "probe_rc": rc,
            "probe_stdout_len": 0,
        }

    result = _parse_output(
        host,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
        rc if rc >= 0 else 1,
    )
    result["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    result["paths"] = paths
    result["ssh_via"] = target
    result["probe_rc"] = rc
    result["probe_stdout_len"] = len(stdout_b or b"")
    if result.get("ok") and not result.get("ram") and not result.get("gpus"):
        result["ok"] = False
        result["error"] = "empty metrics payload"
    if not result.get("ok") and any_path_ok:
        result["reachable"] = True
        live = next((p for p in paths if p.get("ok")), None)
        live_label = (live or {}).get("label") or (live or {}).get("id") or "?"
        prev = (result.get("error") or "").strip()
        if rc == -2 and stderr_b:
            prev = prev or stderr_b.decode("utf-8", errors="replace").strip()
        result["error"] = prev or f"metrics via {target} failed; live path: {live_label}"
    else:
        result["reachable"] = bool(result.get("ok") or any_path_ok)
    return result


def _apply_probe_result(host: str, generation: int, result: dict[str, Any]) -> bool:
    """Commit one probe if the host identity still matches.

    A generation, not merely the hostname, prevents DELETE→ADD from accepting
    a late result belonging to the deleted incarnation.  Failed probes retain
    the last successful payload and only replace connection-state fields.
    """
    if host not in HOSTS or _host_generation.get(host) != generation:
        logger.info("skip stale result for host %s generation %s", host, generation)
        return False
    now = time.time()
    previous = _host_cache.get(host) or {}
    if result.get("ok"):
        committed = {
            **result,
            "connection_ok": True,
            "polling": False,
            "stale": False,
            "last_attempt_at": now,
            "last_success_at": now,
        }
        _host_failures[host] = 0
    else:
        failures = _host_failures.get(host, 0) + 1
        _host_failures[host] = failures
        if previous.get("last_success_at") or previous.get("ok"):
            committed = {
                **previous,
                "host": host,
                "ok": True,
                "connection_ok": False,
                "reachable": bool(result.get("reachable")),
                "polling": False,
                "stale": True,
                "error": result.get("error") or "SSH probe failed",
                "last_attempt_at": now,
                "latency_ms": result.get("latency_ms"),
                "paths": result.get("paths") or previous.get("paths") or [],
            }
        else:
            committed = {
                **result,
                "connection_ok": False,
                "polling": False,
                "stale": False,
                "last_attempt_at": now,
                "last_success_at": None,
            }
    _host_cache[host] = committed
    _cache["ts"] = now
    return True


async def _run_host_probe(host: str, generation: int | None = None) -> None:
    generation = _host_generation.get(host, 0) if generation is None else generation
    current = _host_cache.get(host)
    if current is not None and _host_generation.get(host) == generation:
        current["polling"] = True
        current["last_attempt_at"] = time.time()
    try:
        async with _probe_sem:
            result = await _probe_host(host)
        committed = _apply_probe_result(host, generation, result)
        if committed:
            if result.get("ok"):
                await asyncio.to_thread(_persist_last_good, host, _host_cache[host])
            failures = _host_failures.get(host, 0)
            delay = REMOTE_REFRESH_SEC if not failures else min(
                REMOTE_BACKOFF_MAX_SEC, REMOTE_REFRESH_SEC * (2 ** min(failures, 3))
            )
            _host_next_due[host] = time.monotonic() + delay
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("probe failed for host %s", host)
        _apply_probe_result(host, generation, {"host": host, "ok": False, "error": str(exc)})
        _host_next_due[host] = time.monotonic() + REMOTE_REFRESH_SEC


def _launch_host_probe(host: str, *, immediate: bool = False) -> asyncio.Task[Any] | None:
    task = _host_tasks.get(host)
    if task and not task.done():
        return task
    if host not in HOSTS:
        return None
    if not immediate and time.monotonic() < _host_next_due.get(host, 0.0):
        return None
    generation = _host_generation.setdefault(host, 0)
    task = _track_task(_run_host_probe(host, generation))
    _host_tasks[host] = task
    task.add_done_callback(lambda done, h=host: _host_tasks.pop(h, None) if _host_tasks.get(h) is done else None)
    return task


async def _metrics_scheduler() -> None:
    """Single controlled scheduler; HTTP handlers never launch SSH sweeps."""
    assert _scheduler_stop is not None
    while not _scheduler_stop.is_set():
        now_wall = time.time()
        if now_wall - float(_cache.get("local_ts") or 0.0) >= LOCAL_REFRESH_SEC and not _local_refreshing:
            _track_task(_refresh_local())
        if now_wall - float(_cache.get("zerotier_ts") or 0.0) >= 30.0 and not _zerotier_refreshing:
            _track_task(_refresh_zerotier())
        for host in list(HOSTS):
            _launch_host_probe(host)
        try:
            await asyncio.wait_for(_scheduler_stop.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass


async def _collect_incremental() -> None:
    """Probe all hosts and update _host_cache incrementally as each completes."""

    async def _one(host: str) -> None:
        generation = _host_generation.setdefault(host, 0)
        async with _probe_sem:
            result = await _probe_host(host)
        _apply_probe_result(host, generation, result)

    await asyncio.gather(*[_one(h) for h in HOSTS])


async def _refresh_local() -> None:
    global _local_refreshing
    if _local_refreshing:
        return
    _local_refreshing = True
    try:
        local = await asyncio.to_thread(probe_local)
        _cache["local"] = local
        _cache["local_ts"] = time.time()
    finally:
        _local_refreshing = False


async def _refresh_zerotier() -> None:
    global _zerotier_refreshing
    if _zerotier_refreshing:
        return
    _zerotier_refreshing = True
    try:
        _cache["zerotier"] = await asyncio.to_thread(zerotier_networks)
        _cache["zerotier_ts"] = time.time()
    finally:
        _zerotier_refreshing = False


async def _refresh_cache() -> None:
    global _refreshing, _cache_gen
    if _refreshing:
        return
    _refreshing = True
    gen_at_start = _cache_gen
    try:
        # Local probe is fast — don't wait for the SSH sweep.
        asyncio.create_task(_refresh_local())
        # Incremental update: cache updates as each host probe completes.
        await asyncio.wait_for(_collect_incremental(), timeout=150)
        # Final timestamp update
        if _cache_gen == gen_at_start:
            _cache["ts"] = time.time()
            _cache["gen"] = _cache_gen
    except asyncio.TimeoutError:
        # Keep partial cache if any
        if _cache_gen == gen_at_start:
            _cache["ts"] = time.time()
            _cache["gen"] = _cache_gen
    except Exception:
        logger.exception("refresh_cache failed")
    finally:
        _refreshing = False


async def _startup_probe_local() -> None:
    global _scheduler_stop, _config_lock, _lifecycle_started
    if _lifecycle_started:
        return
    _lifecycle_started = True
    _scheduler_stop = asyncio.Event()
    _config_lock = asyncio.Lock()
    await asyncio.to_thread(_load_last_good_cache)
    for host in HOSTS:
        _host_generation.setdefault(host, 0)
        _host_next_due[host] = 0.0
    _track_task(_refresh_local())
    _track_task(_refresh_zerotier())
    _track_task(_metrics_scheduler())
    # Fire-and-forget first quota refresh so the UI shows real numbers
    # on first paint rather than placeholders.
    _track_task(asyncio.to_thread(quotas_aggregator.refresh_now))
    # Start GPU metrics history collection (every 5 minutes).
    _track_task(_metrics_hist_collector())
    # Do NOT auto-rewrite routes on startup. Overlay breakage is usually Amnezia
    # kill-switch WFP (WSAEACCES), not missing routes — see mesh health API.


async def _metrics_hist_collector() -> None:
    """Collect GPU metrics every 5 minutes for historical tracking."""
    await asyncio.sleep(60)
    while True:
        try:
            servers = [
                _host_cache[h]
                for h in list(HOSTS)
                if h in _host_cache and _host_cache[h].get("ok") and _host_cache[h].get("gpus")
            ]
            for s in servers:
                host = s.get("host", "")
                gpus = s.get("gpus", [])
                if gpus:
                    await asyncio.to_thread(gpu_metrics_history.record_batch, host, gpus)
            await asyncio.to_thread(gpu_metrics_history.cleanup_old)
        except Exception:
            logger.exception("metrics_hist_collector tick failed")
        await asyncio.sleep(300)


@app.post("/api/protect-routes")
async def protect_routes_endpoint(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    out = await asyncio.to_thread(apply_protected_routes)
    zt = await asyncio.to_thread(zerotier_networks)
    return {"ok": True, "protect": out, "zerotier": zt}


@app.get("/chatgpt.pac")
async def chatgpt_pac() -> Response:
    body = proxy_manager.read_pac()
    return Response(
        content=body,
        media_type="application/x-ns-proxy-autoconfig",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/network/public-ip")
async def api_public_ip(force: bool = False) -> dict[str, Any]:
    """Cached public IP / geo / VPN mode. Does not spam external APIs."""
    return await asyncio.to_thread(get_public_ip_status, force=force)


@app.get("/api/network/mesh-health")
async def api_mesh_health(force: bool = False) -> dict[str, Any]:
    """NetBird / ZeroTier TCP health. Diagnose only — never mutates routes."""
    return await asyncio.to_thread(get_mesh_health, force=force)


@app.get("/api/mesh/watcher-status")
async def api_mesh_watcher_status() -> dict[str, Any]:
    """Mesh Route Watcher status (Task Scheduler + heartbeat JSON)."""
    return await asyncio.to_thread(get_watcher_status)


@app.post("/api/mesh/watcher-start")
async def api_mesh_watcher_start(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    """Start GPUProfiler-MeshRouteWatcher via Task Scheduler (enable if needed)."""
    return await asyncio.to_thread(start_mesh_watcher)


# ---------------------------------------------------------------------------
# AI Coding Quotas (MiniMax / Kimi / Codex)
# ---------------------------------------------------------------------------


class _QuotasConfigBody(BaseModel):
    api_key: str | None = None


@app.get("/api/quotas")
async def api_quotas(debug: bool = False) -> dict[str, Any]:
    """Return cached AI Coding Quotas snapshot (refreshes in background)."""
    snap = await asyncio.to_thread(quotas_aggregator.get_snapshot)
    return {
        "ok": True,
        "providers": {
            p: res.to_dict(debug=debug) for p, res in snap.items()
        },
        "config": await asyncio.to_thread(quotas_aggregator.get_minimal_config),
    }


@app.post("/api/quotas/refresh")
async def api_quotas_refresh(debug: bool = False, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    """Force a refresh of all quota collectors and return the new snapshot."""
    snap = await asyncio.to_thread(quotas_aggregator.refresh_now)
    return {
        "ok": True,
        "providers": {
            p: res.to_dict(debug=debug) for p, res in snap.items()
        },
    }


@app.post("/api/quotas/config")
async def api_quotas_config(body: _QuotasConfigBody, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    """Persist MiniMax API key to `.env` (gitignored). Never echoes it back."""
    return await asyncio.to_thread(quotas_aggregator.set_minimax_config, body.api_key)


class _TrackVisitBody(BaseModel):
    username: str = Field(
        default="", max_length=64, pattern=r"^[A-Za-z0-9_.@-]*$",
        description="Opaque display identifier; never a credential",
    )


@app.post("/api/track/visit")
async def track_visit(body: _TrackVisitBody) -> dict[str, Any]:
    """Track a user visit. Returns visit stats."""
    return await asyncio.to_thread(user_tracking.track_visit, body.username)


@app.get("/api/track/users")
async def list_users(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    """List all tracked users."""
    users = await asyncio.to_thread(user_tracking.get_all_users)
    return {"users": users}


class _UserSettingsBody(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    settings: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/track/settings")
async def save_user_settings(
    body: _UserSettingsBody, _admin: None = Depends(require_admin)
) -> dict[str, Any]:
    """Save per-user settings."""
    if len(json.dumps(body.settings, ensure_ascii=False)) > 16_384:
        raise HTTPException(status_code=413, detail="settings payload too large")
    ok = await asyncio.to_thread(user_tracking.update_settings, body.username, body.settings)
    return {"ok": ok}


@app.get("/api/track/settings/{username}")
async def get_user_settings(
    username: str,
    _admin: None = Depends(require_admin),
) -> dict[str, Any]:
    """Get per-user settings."""
    if len(username) > 64 or not re.fullmatch(r"[A-Za-z0-9_.@-]+", username):
        raise HTTPException(status_code=422, detail="invalid username")
    user = await asyncio.to_thread(user_tracking.get_user, username)
    if not user:
        return {"settings": {}}
    import json
    try:
        settings = json.loads(user.get("settings") or "{}")
    except Exception:
        settings = {}
    return {"settings": settings}


@app.get("/api/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    now = time.time()
    zt = list(_cache.get("zerotier") or [])
    user = await asyncio.to_thread(user_config.user_for_ip, _client_ip(request))
    if not user:
        raise HTTPException(status_code=401, detail="select user")
    visible = await asyncio.to_thread(user_config.visible_hosts, user["username"])

    # Build servers list from per-host cache
    servers: list[dict[str, Any]] = []
    for access in visible:
        h = access["hostname"]
        if h in _host_cache:
            row = dict(_host_cache[h])
        else:
            row = {
                "host": h, "ok": False, "reachable": False,
                "error": "загрузка…", "gpus": [], "ram": None,
                "latency_ms": None, "paths": _host_cache_paths(h),
            }
        row["visibility"] = access["visibility"]
        row["owner"] = access["owner"]
        row["can_delete"] = _can_delete_host(user, access)
        servers.append(row)

    if _host_cache:
        return {
            "updated_at": _cache["ts"],
            "cached": True,
            "servers": _with_local(servers),
            "zerotier": zt, "user": user,
        }

    return {
        "updated_at": now,
        "cached": False,
        "servers": _with_local(servers),
        "zerotier": zt, "user": user,
    }


@app.get("/api/diagnostics/ssh")
async def api_ssh_diagnostics(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **ssh_runtime.snapshot()}


@app.get("/api/projects")
async def projects(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    servers = await asyncio.to_thread(discover_projects, HOSTS)
    servers["local"] = await asyncio.to_thread(discover_local_projects)
    return {"servers": servers}


@app.get("/api/gpu-history")
async def gpu_history(host: str, gpu_index: int = None, hours: int = 336) -> dict[str, Any]:
    """Fetch GPU metrics history. If gpu_index is None, returns all GPUs for the host."""
    if host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {host}"}
    if gpu_index is None:
        # Return all GPUs
        data = await asyncio.to_thread(gpu_metrics_history.get_all_history, host, hours)
        return {"ok": True, "host": host, "gpus": data}
    else:
        data = await asyncio.to_thread(gpu_metrics_history.get_history, host, gpu_index, hours)
        return {"ok": True, "host": host, "gpu_index": gpu_index, "history": data}


@app.get("/api/vpn/status")
async def vpn_status() -> dict[str, Any]:
    now = time.time()
    cached = _vpn_cache.get("data")
    # Always re-check proxy ports (cheap TCP) so UI doesn't stick on stale OFF/ON.
    live_proxy = _proxy_ports_up()
    global _pac_ensure_ts
    if live_proxy and (now - _pac_ensure_ts > 20.0 or not _tcp_listening(18080)):
        _pac_ensure_ts = now
        threading.Thread(target=_ensure_chatgpt_pac, daemon=True).start()
    elif (
        not live_proxy
        and (VPN_DIR / "chatgpt-proxy.on").is_file()
        and (now - _pac_ensure_ts > 15.0)
    ):
        # Tunnel died but marker left → clear sticky Yandex proxy once.
        _pac_ensure_ts = now
        threading.Thread(target=_clear_chatgpt_pac, daemon=True).start()

    if cached and now - float(_vpn_cache.get("ts") or 0.0) < VPN_CACHE_SEC:
        patched = dict(cached)
        patched["proxy_on"] = live_proxy
        patched["ssh_vpn_running"] = live_proxy
        modes = dict(patched.get("modes") or {})
        modes["proxy"] = {"on": live_proxy, "label": "ВКЛ" if live_proxy else "ВЫКЛ"}
        patched["modes"] = modes
        if live_proxy:
            patched["active_mode"] = "proxy"
            nb_on = bool(patched.get("netbird_on"))
            patched["active_label"] = "ChatGPT+YT+Comet" + (" + Netbird" if nb_on else "")
        elif patched.get("active_mode") == "proxy":
            if patched.get("full_on"):
                patched["active_mode"] = "full"
                patched["active_label"] = "Full VPN"
            elif patched.get("amnezia_on"):
                patched["active_mode"] = "amnezia"
                patched["active_label"] = "Amnezia"
            elif patched.get("netbird_on"):
                patched["active_mode"] = "netbird"
                patched["active_label"] = "Netbird · МФТИ"
            else:
                patched["active_mode"] = "none"
                patched["active_label"] = "всё выключено"
        return {"ok": True, "cached": True, **patched}

    if not _vpn_status_lock.acquire(blocking=False):
        # NEVER call netbird/sc/ipconfig here — they block the asyncio loop
        # and freeze /ssh/on for 10–20s while status is collecting.
        if cached:
            patched = dict(cached)
            patched.update(
                {
                    "proxy_on": live_proxy,
                    "ssh_vpn_running": live_proxy,
                    "modes": {
                        **(patched.get("modes") or {}),
                        "proxy": {"on": live_proxy, "label": "ВКЛ" if live_proxy else "ВЫКЛ"},
                    },
                }
            )
            if live_proxy and patched.get("active_mode") in (None, "none"):
                patched["active_mode"] = "proxy"
                patched["active_label"] = "ChatGPT+YT+Comet"
            return {"ok": True, "cached": True, "stale": True, **patched}
        return {
            "ok": True,
            "cached": False,
            "partial": True,
            "proxy_on": live_proxy,
            "running": False,
            "quality": "unknown",
            "quality_label": "опрос…",
            "note": "опрос статуса…",
        }

    try:
        data = await asyncio.to_thread(_collect_vpn_status)
        _vpn_cache["ts"] = now
        _vpn_cache["data"] = data
        return {"ok": True, "cached": False, **data}
    finally:
        _vpn_status_lock.release()


def _vpn_action_guard(fn_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Annotate VPN/proxy actions. Do not rewrite overlay routes automatically."""
    result = dict(result)
    result["action"] = fn_name
    # Mesh status is informational only (WFP/kill-switch diagnosis).
    try:
        result["mesh"] = get_mesh_health(force=False).get("summary")
    except Exception:
        result["mesh"] = None
    return result


@app.post("/api/vpn/on")
async def vpn_on() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "Amnezia is managed only via the official AmneziaVPN app (read-only in GPU Profiler).",
    }


@app.post("/api/vpn/off")
async def vpn_off() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "Amnezia is managed only via the official AmneziaVPN app (read-only in GPU Profiler).",
    }


@app.post("/api/vpn/ssh/on")
async def vpn_ssh_on(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    ok, out, already = await asyncio.to_thread(_ssh_vpn_start_now)
    # Soft-invalidate so next status poll re-reads ports quickly.
    _vpn_cache["ts"] = 0.0
    if not ok:
        return _vpn_action_guard(
            "vpn_ssh_on", {"ok": False, "error": out or "не удалось включить ChatGPT+YT+Comet"}
        )
    up = already or _proxy_ports_up()
    return _vpn_action_guard(
        "vpn_ssh_on",
        {
            "ok": True,
            "message": "открыл Яндекс → chatgpt.com (через прокси)" if up else "туннель + Яндекс…",
            "proxy_on": True,  # optimistic — UI shows ON immediately
            "starting": not up,
            "active_label": "ChatGPT+YT+Comet",
        },
    )


@app.post("/api/vpn/ssh/off")
async def vpn_ssh_off(_admin: None = Depends(require_admin)) -> dict[str, Any]:
    ok, out = await asyncio.to_thread(_ssh_vpn_stop_now)
    _vpn_cache["ts"] = 0.0
    _vpn_cache["data"] = None
    if not ok:
        return _vpn_action_guard(
            "vpn_ssh_off", {"ok": False, "error": out or "не удалось выключить SSH VPN"}
        )
    return _vpn_action_guard("vpn_ssh_off", {"ok": True, "message": "SSH VPN выключается"})


@app.post("/api/vpn/full/on")
async def vpn_full_on() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "Legacy Full TUN VPN disabled in UI. Use AmneziaVPN app for full VPN.",
    }


@app.post("/api/vpn/full/off")
async def vpn_full_off() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "Legacy Full TUN VPN disabled in UI. Use AmneziaVPN app for full VPN.",
    }


@app.post("/api/vpn/netbird/on")
async def vpn_netbird_on() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "NetBird is managed independently (GPU Profiler is read-only).",
    }


@app.post("/api/vpn/netbird/off")
async def vpn_netbird_off() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "NetBird is managed independently (GPU Profiler is read-only).",
    }


class OpenCursorBody(BaseModel):
    host: str
    path: str = Field(min_length=1)
    uri: str | None = None


@app.post("/api/open-cursor")
async def open_cursor(body: OpenCursorBody, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    if body.host == "local":
        return open_local_project(body.path)
    if body.host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {body.host}"}
    return open_remote_project(body.host, body.path, uri=body.uri)


class AgentChatBody(BaseModel):
    host: str
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    uri: str | None = None


@app.post("/api/open-agent")
async def open_agent(body: OpenCursorBody, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    """Start (or reuse) an SDK agent session bound to the remote project."""
    if body.host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {body.host}"}
    try:
        session = await sdk_agent.ensure_session(body.host, body.path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "host": session.host,
        "path": session.path,
        "mode": "sdk",
        "history": session.history[-40:],
    }


@app.get("/api/agent/session")
async def agent_session(host: str, path: str, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    if host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {host}"}
    info = sdk_agent.session_info(host, path)
    if not info:
        return {"ok": False, "error": "no session"}
    return {"ok": True, **info}


@app.post("/api/agent/chat")
async def agent_chat(body: AgentChatBody, _admin: None = Depends(require_admin)) -> StreamingResponse:
    if body.host not in HOSTS:
        async def err_gen():  # noqa: ANN202
            yield f"data: {json.dumps({'type': 'error', 'text': 'unknown host'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(err_gen(), media_type="text/event-stream")

    async def gen():  # noqa: ANN202
        try:
            async for ev in sdk_agent.send_and_stream(body.host, body.path, body.message):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/agent/unlock")
async def agent_unlock(body: OpenCursorBody, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    if body.host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {body.host}"}
    ok = await sdk_agent.force_unlock(body.host, body.path)
    return {"ok": ok}


def _parse_ssh_config() -> list[dict[str, str]]:
    """Parse ~/.ssh/config and return list of Host entries."""
    ssh_config = Path.home() / ".ssh" / "config"
    if not ssh_config.is_file():
        return []
    hosts: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    current_aliases: list[str] = []
    try:
        for line in ssh_config.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) < 2:
                continue
            key, value = parts[0].lower(), parts[1].strip()
            if key == "host":
                if current and current_aliases:
                    for alias in current_aliases:
                        hosts.append({**current, "alias": alias})
                aliases = value.split()
                current_aliases = [a for a in aliases if "*" not in a and "?" not in a]
                current = {}
            elif current is not None:
                if key == "hostname":
                    current["hostname"] = value
                elif key == "user":
                    current["user"] = value
                elif key == "port":
                    current["port"] = value
        if current and current_aliases:
            for alias in current_aliases:
                hosts.append({**current, "alias": alias})
    except Exception:
        pass
    return hosts


@app.get("/api/ssh-hosts")
async def api_ssh_hosts(request: Request) -> dict[str, Any]:
    """Return SSH hosts from ~/.ssh/config, excluding already monitored hosts."""
    _current_user(request)
    hosts = _parse_ssh_config()
    monitored = set(HOSTS)
    return {"hosts": [h for h in hosts if h["alias"] not in monitored]}


class _AddHostBody(BaseModel):
    hostname: str = Field(min_length=1, max_length=64)
    network: str = Field(default="ssh")
    ip: str = Field(min_length=1, max_length=256)
    port: int = Field(default=22, ge=1, le=65535)
    ssh_target: str | None = None


def _write_host_paths_atomic(data: dict[str, Any]) -> None:
    paths_file = ROOT / "host_paths.json"
    temporary = paths_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(paths_file)


def _valid_ssh_target(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_.:-]+", value)) and not value.startswith("-")


@app.post("/api/hosts")
async def api_add_host(body: _AddHostBody, request: Request) -> dict[str, Any]:
    """Add a shared admin host or a private host owned by the current user."""
    user = _current_user(request)
    if not re.match(r"^[a-zA-Z0-9_-]+$", body.hostname):
        return {"ok": False, "error": "hostname: letters, digits, _ and - only"}
    ssh_target = body.ssh_target or f"reedgern@{body.ip}"
    if not _valid_ssh_target(ssh_target) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", body.ip):
        return {"ok": False, "error": "invalid IP/SSH target"}
    path_entry: dict[str, Any] = {
        "id": f"ssh-{body.hostname}",
        "label": f"SSH ({body.ip})",
        "kind": "ssh",
        "ssh_target": ssh_target,
        "ip": body.ip,
        "port": body.port,
    }

    lock = _config_lock or asyncio.Lock()
    async with lock:
        if body.hostname in HOSTS:
            return {"ok": False, "error": f"host '{body.hostname}' already exists"}
        if await asyncio.to_thread(user_config.get_host, body.hostname):
            return {"ok": False, "error": f"host '{body.hostname}' already exists"}
        await asyncio.to_thread(
            user_config.add_host, body.hostname, user["username"], [path_entry],
            shared=bool(user["is_admin"]),
        )
        await asyncio.to_thread(_write_host_paths_atomic, user_config.all_hosts())
        HOSTS.append(body.hostname)
        _host_generation[body.hostname] = _host_generation.get(body.hostname, 0) + 1
        _host_next_due[body.hostname] = 0.0
    _cache["ts"] = time.time()

    placeholder = {
        "host": body.hostname,
        "ok": False,
        "reachable": False,
        "error": "загрузка…",
        "gpus": [],
        "ram": None,
        "latency_ms": None,
        "paths": [path_entry],
    }
    _host_cache[body.hostname] = placeholder

    _launch_host_probe(body.hostname, immediate=True)

    return {
        "ok": True, "host": body.hostname, "paths": [path_entry],
        "visibility": "shared" if user["is_admin"] else "private",
        "owner": user["username"], "can_delete": True,
    }


@app.delete("/api/hosts/{hostname}")
async def api_delete_host(hostname: str, request: Request) -> dict[str, Any]:
    """Delete an owned private host, or any host when current user is admin."""
    user = _current_user(request)
    record = await asyncio.to_thread(user_config.get_host, hostname)
    if not _can_delete_host(user, record):
        raise HTTPException(status_code=403, detail="этот сервер нельзя удалить")
    if hostname not in HOSTS:
        return {"ok": False, "error": f"host '{hostname}' not found"}
    lock = _config_lock or asyncio.Lock()
    async with lock:
        if hostname not in HOSTS:
            return {"ok": False, "error": f"host '{hostname}' not found"}
        await asyncio.to_thread(user_config.delete_host, hostname)
        await asyncio.to_thread(_write_host_paths_atomic, user_config.all_hosts())
        HOSTS.remove(hostname)
        _host_generation[hostname] = _host_generation.get(hostname, 0) + 1
    task = _host_tasks.pop(hostname, None)
    if task and not task.done():
        task.cancel()
    _host_next_due.pop(hostname, None)
    _host_failures.pop(hostname, None)
    _cache["ts"] = time.time()
    _host_cache.pop(hostname, None)
    try:
        await asyncio.to_thread(_last_good_path(hostname).unlink, True)
    except OSError:
        logger.exception("failed to remove persisted cache for %s", hostname)

    return {"ok": True, "host": hostname}


def _discover_network_peers() -> dict[str, Any]:
    """Discover available peers from ZeroTier and NetBird."""
    zt_cli = Path(r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat")
    result: dict[str, Any] = {
        "netbird": {"peers": [], "error": None},
        "zerotier": {"peers": [], "error": None},
    }

    # --- ZeroTier ---
    if zt_cli.is_file():
        try:
            out = subprocess.run(
                [str(zt_cli), "listnetworks"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            for ln in out.stdout.splitlines():
                if "<nwid>" in ln or not ln.startswith("200 listnetworks "):
                    continue
                parts = ln.split()
                if len(parts) < 8:
                    continue
                ips = parts[-1]
                if ips and ips != "-":
                    ip = ips.split("/")[0]
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                        result["zerotier"]["peers"].append(
                            {"ip": ip, "name": f"zt-{ip.split('.')[-1]}"}
                        )
        except Exception as exc:
            result["zerotier"]["error"] = str(exc)

        try:
            out = subprocess.run(
                [str(zt_cli), "listpeers"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            seen = {p["ip"] for p in result["zerotier"]["peers"]}
            for ln in out.stdout.splitlines():
                if "200 listpeers" not in ln or "<id>" in ln:
                    continue
                parts = ln.split()
                if len(parts) < 6:
                    continue
                ip = parts[-1]
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip) and ip not in seen:
                    seen.add(ip)
                    result["zerotier"]["peers"].append(
                        {"ip": ip, "name": f"zt-peer-{ip.split('.')[-1]}"}
                    )
        except Exception as exc:
            result["zerotier"]["error"] = (result["zerotier"].get("error") or "") + str(exc)
    else:
        result["zerotier"]["error"] = "zerotier-cli not found"

    # --- NetBird ---
    nb_cli = Path(r"C:\Program Files\NetBird\netbird.exe")
    if nb_cli.is_file():
        try:
            out = subprocess.run(
                [str(nb_cli), "status"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            for ln in out.stdout.splitlines():
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", ln)
                if m and ("Peers" in ln or "Connected" in ln or "peer" in ln.lower()):
                    ip = m.group(1)
                    if ip.startswith("100."):
                        result["netbird"]["peers"].append(
                            {"ip": ip, "name": f"nb-{ip.split('.')[-1]}"}
                        )
        except Exception as exc:
            result["netbird"]["error"] = str(exc)
    else:
        result["netbird"]["error"] = "netbird CLI not found"

    # Deduplicate by IP within each network
    for net_key in ("netbird", "zerotier"):
        seen = set()
        deduped = []
        for p in result[net_key]["peers"]:
            if p["ip"] not in seen:
                seen.add(p["ip"])
                deduped.append(p)
        result[net_key]["peers"] = deduped

    return result


async def _shutdown_sdk() -> None:
    global _lifecycle_started
    if _scheduler_stop is not None:
        _scheduler_stop.set()
    for task in list(_host_tasks.values()):
        task.cancel()
    current = asyncio.current_task()
    tasks = [task for task in list(_background_tasks) if task is not current and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _host_tasks.clear()
    await sdk_agent.close_client()
    _lifecycle_started = False


@app.get("/api/fs/roots")
async def api_fs_roots(host: str, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    if host == "local":
        data = local_fs_list(None)
        return {
            "ok": True,
            "home": data.get("home"),
            "cwd": data.get("path"),
        }
    if host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {host}"}
    return await fs_roots(host)


@app.get("/api/fs/list")
async def api_fs_list(host: str, path: str | None = None, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    if host == "local":
        return local_fs_list(path)
    if host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {host}"}
    return await fs_list(host, path)


@app.get("/api/fs/repos")
async def api_fs_repos(host: str, _admin: None = Depends(require_admin)) -> dict[str, Any]:
    if host == "local":
        return {"ok": True, "repos": [], "home": local_fs_list(None).get("home")}
    if host not in HOSTS:
        return {"ok": False, "error": f"unknown host: {host}"}
    return await fs_repos(host)
