"""SSH filesystem helpers for folder / repo browsing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from cursor_projects import is_safe_remote_path
import ssh_runtime

ROOT = Path(__file__).resolve().parent
FS_SCRIPT = (ROOT / "remote_fs.py").read_bytes()
SSH_TIMEOUT_SEC = 16


async def _ssh_python(host: str, args: list[str]) -> dict[str, Any]:
    started, _waited = await asyncio.to_thread(ssh_runtime.acquire, host, "filesystem")
    timed_out = False
    failed = False
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_TIMEOUT_SEC}",
            "-o", "StrictHostKeyChecking=accept-new", host, "python3", "-", *args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=FS_SCRIPT), timeout=SSH_TIMEOUT_SEC + 8
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": "timeout"}

        out = stdout_b.decode("utf-8", errors="replace").strip()
        err = stderr_b.decode("utf-8", errors="replace").strip()
        if not out:
            failed = True
            return {"ok": False, "error": err or f"ssh exit {proc.returncode}"}
        line = out.splitlines()[-1]
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            failed = True
            return {"ok": False, "error": err or out[:300]}
        if isinstance(data, dict):
            return data
        failed = True
        return {"ok": False, "error": "bad response"}
    except asyncio.CancelledError:
        failed = True
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    finally:
        ssh_runtime.release(host, started, timeout=timed_out, error=failed)


async def fs_roots(host: str) -> dict[str, Any]:
    return await _ssh_python(host, ["roots"])


async def fs_list(host: str, path: str | None = None) -> dict[str, Any]:
    if path is None:
        return await _ssh_python(host, ["list"])
    if not is_safe_remote_path(path):
        return {"ok": False, "error": "unsafe path"}
    return await _ssh_python(host, ["list", path])


async def fs_repos(host: str, path: str | None = None) -> dict[str, Any]:
    if path is None:
        return await _ssh_python(host, ["repos"])
    if not is_safe_remote_path(path):
        return {"ok": False, "error": "unsafe path"}
    return await _ssh_python(host, ["repos", path])
