#!/usr/bin/env python3
"""Push a local GPU snapshot to a GPU Fleet controller.

The agent needs only outbound HTTP(S). It never opens a listening port and
never receives a controller SSH key.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "remote_probe.py"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def collect() -> str:
    result = subprocess.run(
        [sys.executable, str(PROBE)], capture_output=True, text=True, timeout=45, check=False
    )
    if result.returncode:
        raise RuntimeError((result.stderr or f"probe exited {result.returncode}").strip())
    if not result.stdout.strip():
        raise RuntimeError("probe returned no data")
    return result.stdout


def send(endpoint: str, token: str, host: str, payload: str) -> None:
    data = json.dumps(
        {"host": host, "sent_at": time.time(), "probe_output": payload},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-GPU-Agent-Token": token,
            "User-Agent": "gpu-profiler-agent/1",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"controller returned HTTP {response.status}")


def main() -> None:
    endpoint = required("GPU_FLEET_ENDPOINT")
    token = required("GPU_FLEET_AGENT_TOKEN")
    host = required("GPU_FLEET_HOST")
    interval = max(3.0, float(os.getenv("GPU_FLEET_INTERVAL_SEC", "5")))
    while True:
        started = time.monotonic()
        try:
            send(endpoint, token, host, collect())
            print("heartbeat sent", flush=True)
        except (OSError, RuntimeError, subprocess.SubprocessError, urllib.error.URLError) as exc:
            print(f"heartbeat failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(max(0.2, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
