"""ChatGPT selective proxy manager — PID-tracked, no mass process kills."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "proxy"
VPN_DIR = Path.home() / ".ssh" / "timeweb-vpn"
PID_FILE = VPN_DIR / "sing-box-chatgpt.pid"
LEGACY_PID_FILE = VPN_DIR / "sing-box.pid"
MARKER = VPN_DIR / "chatgpt-proxy.on"
PAC_FILE = VPN_DIR / "chatgpt.pac"


def _tcp_listening(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _run_ps_file(script: Path, timeout: float = 90.0) -> str:
    if not script.is_file():
        return f"missing script: {script}"
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            cwd=str(script.parent),
        )
        text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
        return text[-2000:]
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception as exc:
        return str(exc)


def is_running() -> bool:
    return _tcp_listening(10808)


def status() -> dict[str, Any]:
    running = is_running()
    pid = None
    for path in (PID_FILE, LEGACY_PID_FILE):
        try:
            if path.is_file():
                raw = (path.read_text(encoding="utf-8") or "").strip()
                if raw.isdigit():
                    pid = int(raw)
                    break
        except OSError:
            pass
    return {
        "on": running,
        "port": 10808,
        "pid": pid,
        "pac_url": "http://127.0.0.1:8765/chatgpt.pac",
        "pac_fallback": "http://127.0.0.1:18080/chatgpt.pac",
        "marker": MARKER.is_file(),
        "pac_file_exists": PAC_FILE.is_file(),
    }


def start() -> dict[str, Any]:
    if is_running():
        return {"ok": True, "already": True, "message": "ChatGPT proxy already running", **status()}
    # Fire-and-forget style: script returns quickly after port is up.
    out = _run_ps_file(SCRIPTS / "start_chatgpt_proxy.ps1", timeout=45.0)
    # Brief wait if script started process asynchronously
    deadline = time.time() + 8.0
    while time.time() < deadline and not is_running():
        time.sleep(0.25)
    ok = is_running()
    return {
        "ok": ok,
        "already": False,
        "message": out or ("ChatGPT PROXY ON" if ok else "failed to start"),
        **status(),
    }


def stop() -> dict[str, Any]:
    out = _run_ps_file(SCRIPTS / "stop_chatgpt_proxy.ps1", timeout=45.0)
    deadline = time.time() + 6.0
    while time.time() < deadline and is_running():
        time.sleep(0.2)
    still = is_running()
    return {
        "ok": not still,
        "message": out or ("ChatGPT PROXY OFF" if not still else "still running"),
        **status(),
    }


def read_pac() -> str:
    if PAC_FILE.is_file():
        return PAC_FILE.read_text(encoding="utf-8")
    return "function FindProxyForURL(u,h){return 'DIRECT';}"
