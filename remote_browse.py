"""SSH filesystem helpers for folder / repo browsing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from cursor_projects import is_safe_remote_path

ROOT = Path(__file__).resolve().parent
FS_SCRIPT = (ROOT / "remote_fs.py").read_bytes()
SSH_TIMEOUT_SEC = 16


async def _ssh_python(host: str, args: list[str]) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={SSH_TIMEOUT_SEC}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        host,
        "python3",
        "-",
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=FS_SCRIPT), timeout=SSH_TIMEOUT_SEC + 8
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "error": "timeout"}

    out = stdout_b.decode("utf-8", errors="replace").strip()
    err = stderr_b.decode("utf-8", errors="replace").strip()
    if not out:
        return {"ok": False, "error": err or f"ssh exit {proc.returncode}"}
    # script prints one JSON object; take last non-empty line if warnings exist
    line = out.splitlines()[-1]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": err or out[:300]}
    if isinstance(data, dict):
        return data
    return {"ok": False, "error": "bad response"}


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
