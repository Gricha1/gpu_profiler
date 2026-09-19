"""Multi-path SSH / reachability probes for monitor hosts."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PATHS_FILE = ROOT / "host_paths.json"
PROTECTED_FILE = ROOT / "protected_nets.json"
ZT_CLI = Path(r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat")
PROTECT_SCRIPT = ROOT / "protect_routes.ps1"

_path_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
PATH_CACHE_SEC = 25.0
_paths_mtime: float = 0.0


def _paths_file_fresh() -> bool:
    global _paths_mtime
    try:
        m = PATHS_FILE.stat().st_mtime
    except OSError:
        return True
    if m != _paths_mtime:
        _paths_mtime = m
        _path_cache.clear()
        return False
    return True


def load_host_paths() -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(PATHS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}
    except Exception:
        pass
    return {}


def load_protected_nets() -> dict[str, Any]:
    try:
        data = json.loads(PROTECTED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def apply_protected_routes() -> str:
    """Re-apply ZT/NetBird protected routes. Never raises."""
    if not PROTECT_SCRIPT.is_file():
        return "no script"
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROTECT_SCRIPT),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        return ((out.stdout or "") + (out.stderr or "")).strip()[-1500:]
    except Exception as exc:  # noqa: BLE001
        return f"protect_routes error: {exc}"


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> tuple[bool, float | None]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return False, None


def _ssh_ok(
    target: str,
    extra_opts: list[str] | None = None,
    timeout_sec: int = 6,
) -> tuple[bool, float | None, str]:
    t0 = time.perf_counter()
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_sec}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if extra_opts:
        cmd.extend(extra_opts)
    cmd.extend([target, "echo", "OK"])
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 8,
            check=False,
        )
        ms = round((time.perf_counter() - t0) * 1000, 1)
        text = ((out.stdout or "") + (out.stderr or "")).strip()
        ok = out.returncode == 0 and any(
            line.strip() == "OK" for line in (out.stdout or "").splitlines()
        )
        err = ""
        if not ok:
            err = text.splitlines()[-1] if text else f"ssh exit {out.returncode}"
        return ok, ms if ok else None, err
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)


def _anydesk_seen(anydesk_id: str) -> dict[str, Any]:
    """Best-effort: roster / recent config mentions the id (not a live session)."""
    paths = [
        Path.home() / "AppData" / "Roaming" / "AnyDesk" / "user.conf",
        Path(r"C:\ProgramData\AnyDesk\system.conf"),
    ]
    seen = False
    for p in paths:
        try:
            if p.is_file() and anydesk_id in p.read_text(encoding="utf-8", errors="ignore"):
                seen = True
                break
        except OSError:
            pass
    return {
        "ok": False,
        "seen": seen,
        "ms": None,
        "detail": "in roster (not a live session check)" if seen else "not in local roster",
        "live_session": False,
    }


def zerotier_networks() -> list[dict[str, Any]]:
    if not ZT_CLI.is_file():
        return []
    try:
        out = subprocess.run(
            [str(ZT_CLI), "listnetworks"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in (out.stdout or "").splitlines():
        # 200 listnetworks <nwid> <name> <mac> <status> <type> <dev> <ips>
        parts = line.split()
        if len(parts) < 8 or parts[0] != "200" or parts[1] != "listnetworks":
            continue
        if parts[2] == "<nwid>":
            continue
        nwid = parts[2]
        # name may be empty
        # Format: 200 listnetworks nwid name mac status type dev ips...
        # When name empty: nwid '' mac ...
        rest = line.split(None, 3)[-1] if len(line.split(None, 3)) >= 4 else ""
        # Prefer regex-ish split from known statuses
        status = "UNKNOWN"
        for st in ("OK", "ACCESS_DENIED", "NOT_FOUND", "REQUESTING_CONFIGURATION", "CLIENT_TOO_OLD"):
            if f" {st} " in f" {line} ":
                status = st
                break
        ips = []
        if parts[-1] != "-" and "/" in parts[-1]:
            ips = [parts[-1]]
        elif len(parts) >= 9 and parts[-1] != "-":
            ips = parts[8:]
        name = ""
        if status in line:
            before = line.split(f" {status} ")[0]
            # strip prefix
            bit = before.split("listnetworks", 1)[-1].strip()
            toks = bit.split()
            if toks:
                name = " ".join(toks[1:]) if len(toks) > 1 else ""
        rows.append(
            {
                "nwid": nwid,
                "name": name.strip(),
                "status": status,
                "ips": ips,
                "ok": status == "OK",
            }
        )
    return rows


def probe_path(path: dict[str, Any]) -> dict[str, Any]:
    pid = str(path.get("id") or "")
    label = str(path.get("label") or pid)
    kind = str(path.get("kind") or "ssh")
    ip = path.get("ip")
    port = path.get("port")
    if ip is None and isinstance(path.get("tcp"), (list, tuple)) and path.get("tcp"):
        ip = path["tcp"][0]
        if port is None and len(path["tcp"]) > 1:
            port = path["tcp"][1]
    if port is None:
        port = 22 if kind == "ssh" else None
    base = {
        "id": pid,
        "label": label,
        "kind": kind,
        "protected": bool(path.get("protected")),
        "ok": False,
        "ms": None,
        "detail": "",
        "ssh_target": path.get("ssh_target"),
        "ip": str(ip) if ip is not None else None,
        "port": int(port) if port is not None else None,
        "via": path.get("via"),
        "host_name": path.get("host"),
    }
    if kind == "anydesk":
        info = _anydesk_seen(str(path.get("anydesk_id") or ""))
        base.update(info)
        base["detail"] = info.get("detail") or ""
        base["ip"] = str(path.get("anydesk_id") or ip or "")
        return base

    tcp = path.get("tcp")
    if isinstance(tcp, (list, tuple)) and len(tcp) == 2:
        ok, ms = _tcp_ok(str(tcp[0]), int(tcp[1]), timeout=1.5)
        base["tcp_ok"] = ok
        base["tcp_ms"] = ms
        if not ok:
            base["detail"] = f"tcp {tcp[0]}:{tcp[1]} closed"
            return base
        # TCP connect alone is NOT SSH. Only skip SSH when explicitly tcp_only.
        if (
            path.get("tcp_only")
            and not path.get("prefer_for_probe")
            and not path.get("require_ssh")
        ):
            base["ok"] = True
            base["ms"] = ms
            base["detail"] = "tcp open"
            return base

    opts = path.get("ssh_opts") if isinstance(path.get("ssh_opts"), list) else []
    target = str(path.get("ssh_target") or "")
    if not target:
        base["detail"] = "no ssh_target"
        return base
    timeout = 10 if any("ProxyJump" in str(x) and "none" not in str(x) for x in opts) or target == "lab_comp" else 5
    if target == "lab_comp":
        timeout = 12
    ok, ms, err = _ssh_ok(target, [str(x) for x in opts], timeout_sec=timeout)
    base["ok"] = ok
    base["ms"] = ms
    base["detail"] = "ok" if ok else (err or "fail")
    return base


def probe_host_paths(host: str, *, force: bool = False) -> list[dict[str, Any]]:
    _paths_file_fresh()
    paths = load_host_paths().get(host) or []
    if not paths:
        return []
    now = time.time()
    if not force:
        cached = _path_cache.get(host)
        if cached and now - cached[0] < PATH_CACHE_SEC:
            return cached[1]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(paths))) as pool:
        futs = {pool.submit(probe_path, p): p for p in paths}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                p = futs[fut]
                results.append(
                    {
                        "id": p.get("id"),
                        "label": p.get("label"),
                        "ok": False,
                        "detail": str(exc),
                        "protected": bool(p.get("protected")),
                        "ip": p.get("ip"),
                        "port": p.get("port"),
                        "via": p.get("via"),
                        "kind": p.get("kind"),
                    }
                )
    # stable order as in config
    order = {str(p.get("id")): i for i, p in enumerate(paths)}
    results.sort(key=lambda r: order.get(str(r.get("id")), 999))
    _path_cache[host] = (now, results)
    return results


def best_ssh_target(host: str, path_results: list[dict[str, Any]] | None = None) -> tuple[str, list[str]]:
    """Return (ssh_target, extra_opts) for metrics probe."""
    cfg = load_host_paths().get(host) or []
    results = path_results if path_results is not None else probe_host_paths(host)
    by_id = {str(r.get("id")): r for r in results}

    # Prefer configured prefer_for_probe if alive
    for p in cfg:
        if p.get("prefer_for_probe") and by_id.get(str(p.get("id")), {}).get("ok"):
            return str(p.get("ssh_target") or host), list(p.get("ssh_opts") or [])

    # Prefer any protected live ssh path
    for p in cfg:
        rid = str(p.get("id"))
        if p.get("kind", "ssh") != "ssh":
            continue
        if p.get("protected") and by_id.get(rid, {}).get("ok"):
            return str(p.get("ssh_target") or host), list(p.get("ssh_opts") or [])

    # Any live ssh path
    for p in cfg:
        rid = str(p.get("id"))
        if p.get("kind", "ssh") != "ssh":
            continue
        if by_id.get(rid, {}).get("ok"):
            return str(p.get("ssh_target") or host), list(p.get("ssh_opts") or [])

    # Fallback: ssh host alias
    return host, []
