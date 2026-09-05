"""Probe this Windows machine: RAM, GPU VRAM/util, top RAM consumers."""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import socket
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def _run(cmd: list[str], timeout: float = 6.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.returncode or 0, p.stdout or "", p.stderr or ""
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _ram() -> dict[str, Any] | None:
    try:
        import psutil
    except ImportError:
        return None
    vm = psutil.virtual_memory()
    return {
        "total_bytes": int(vm.total),
        "used_bytes": int(vm.used),
        "available_bytes": int(vm.available),
        "used_pct": round(float(vm.percent), 1),
    }


def _cpu_pct() -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    return round(float(psutil.cpu_percent(interval=None)), 1)


def _ram_top(limit: int = 15) -> list[dict[str, Any]]:
    try:
        import psutil
    except ImportError:
        return []

    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            info = proc.info
            mi = info.get("memory_info")
            if mi is None:
                continue
            rss = int(getattr(mi, "rss", 0) or 0)
            if rss <= 0:
                continue
            rows.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or "?"),
                    "user": str(info.get("username") or "?").split("\\")[-1],
                    "rss_bytes": rss,
                    "rss_mib": round(rss / (1024 * 1024), 1),
                }
            )
        except (psutil.Error, OSError, TypeError, ValueError):
            continue

    rows.sort(key=lambda r: r["rss_bytes"], reverse=True)
    return rows[:limit]


def _parse_mem_mib(raw: str) -> float | None:
    s = (raw or "").strip().strip("[]")
    if not s or s.upper() == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _windows_gpu_dedicated_mib_by_pid() -> dict[int, float]:
    """Per-process dedicated GPU memory via Windows performance counters.

    On WDDM laptops nvidia-smi often reports Used GPU Memory as N/A; Task Manager
    still shows Dedicated Usage — these counters are the same source.
    """
    if os.name != "nt":
        return {}
    # Prefer typeperf (faster/lighter than PowerShell) when available.
    rc, out, _ = _run(
        [
            "typeperf",
            r"\GPU Process Memory(*)\Dedicated Usage",
            "-sc",
            "1",
        ],
        timeout=8.0,
    )
    by_pid: dict[int, float] = defaultdict(float)
    if rc == 0 and out.strip():
        # CSV: "timestamp","\\...\pid_123_luid_...","value",...
        for row in csv.reader(io.StringIO(out)):
            if len(row) < 2:
                continue
            for cell in row[1:]:
                low = cell.lower()
                if "pid_" not in low:
                    continue
                # Instance may be in header row only for typeperf — values are numeric columns.
                # typeperf layout: first data line has headers with instance paths, second has values.
                pass
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if len(lines) >= 2:
            try:
                headers = next(csv.reader([lines[0]]))
                values = next(csv.reader([lines[-1]]))
            except Exception:
                headers, values = [], []
            for h, v in zip(headers[1:], values[1:]):
                m = re.search(r"pid_(\d+)", h, re.I)
                if not m:
                    continue
                try:
                    by_pid[int(m.group(1))] += float(v) / (1024 * 1024)
                except ValueError:
                    continue
        if by_pid:
            return {pid: round(mib, 1) for pid, mib in by_pid.items() if mib > 0}

    # Fallback: PowerShell Get-Counter
    ps = (
        "Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty CounterSamples "
        "| ForEach-Object { $_.InstanceName + '|' + $_.CookedValue }"
    )
    rc, out, _ = _run(["powershell", "-NoProfile", "-Command", ps], timeout=12.0)
    if rc != 0 or not out.strip():
        return {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        inst, _, val = line.partition("|")
        m = re.search(r"pid_(\d+)", inst, re.I)
        if not m:
            continue
        try:
            by_pid[int(m.group(1))] += float(val.strip()) / (1024 * 1024)
        except ValueError:
            continue
    return {pid: round(mib, 1) for pid, mib in by_pid.items() if mib > 0}


def _gpus() -> list[dict[str, Any]]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []

    rc, out, _err = _run(
        [
            smi,
            "--query-gpu=index,name,uuid,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc != 0 or not out.strip():
        return []

    gpus: list[dict[str, Any]] = []
    uuid_to_idx: dict[str, int] = {}
    for row in csv.reader(io.StringIO(out)):
        if len(row) < 6:
            continue
        try:
            idx = int(row[0].strip())
            name = row[1].strip()
            uuid = row[2].strip()
            mem_used = float(row[3].strip())
            mem_total = float(row[4].strip())
            util = float(row[5].strip())
        except ValueError:
            continue
        uuid_to_idx[uuid] = idx
        mem_pct = round(100.0 * mem_used / mem_total, 1) if mem_total else 0.0
        gpus.append(
            {
                "index": idx,
                "name": name,
                "uuid": uuid,
                "mem_used_mib": round(mem_used, 1),
                "mem_total_mib": round(mem_total, 1),
                "mem_pct": mem_pct,
                "util_pct": round(util, 1),
                "processes": [],
                "users": [],
            }
        )

    rc2, out2, _ = _run(
        [
            smi,
            "--query-compute-apps=pid,gpu_uuid,used_gpu_memory,process_name",
            "--format=csv,noheader,nounits",
        ]
    )
    procs_by_idx: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if rc2 == 0 and out2.strip():
        try:
            import psutil
        except ImportError:
            psutil = None  # type: ignore[assignment]

        for row in csv.reader(io.StringIO(out2)):
            if len(row) < 3:
                continue
            try:
                pid = int(row[0].strip())
            except ValueError:
                continue
            uuid = row[1].strip()
            mem = _parse_mem_mib(row[2])
            proc_path = row[3].strip() if len(row) > 3 else ""
            idx = uuid_to_idx.get(uuid)
            if idx is None and len(gpus) == 1:
                idx = int(gpus[0]["index"])
            if idx is None:
                continue
            name = Path(proc_path).name if proc_path else "?"
            user = "?"
            if psutil is not None:
                try:
                    p = psutil.Process(pid)
                    if name == "?" or not name:
                        name = p.name()
                    user = (p.username() or "?").split("\\")[-1]
                except (psutil.Error, OSError):
                    pass
            procs_by_idx[idx].append(
                {
                    "pid": pid,
                    "name": name or "?",
                    "user": user,
                    "mem_mib": round(mem, 1) if mem is not None else None,
                    "mem_unknown": mem is None,
                }
            )

    # Fill WDDM N/A VRAM from Windows dedicated GPU memory counters.
    need_fill = any(
        p.get("mem_mib") is None
        for procs in procs_by_idx.values()
        for p in procs
    )
    if need_fill:
        win_mem = _windows_gpu_dedicated_mib_by_pid()
        for procs in procs_by_idx.values():
            for p in procs:
                if p.get("mem_mib") is not None:
                    continue
                mib = win_mem.get(int(p["pid"]))
                if mib is not None:
                    p["mem_mib"] = mib
                    p["mem_unknown"] = False
                    p["mem_source"] = "wddm"

    by_idx = {g["index"]: g for g in gpus}
    for idx, procs in procs_by_idx.items():
        g = by_idx.get(idx)
        if not g:
            continue
        g["processes"] = sorted(
            procs,
            key=lambda p: (0 if p.get("mem_mib") is None else 1, float(p.get("mem_mib") or 0)),
            reverse=True,
        )
        by_name: dict[str, float] = defaultdict(float)
        for p in g["processes"]:
            by_name[p["name"]] += float(p["mem_mib"] or 0)
        g["users"] = [
            {"user": n, "mem_mib": round(m, 1), "mem_gib": round(m / 1024, 2)}
            for n, m in sorted(by_name.items(), key=lambda kv: -kv[1])
        ]

    return gpus


def probe_local() -> dict[str, Any]:
    t0 = time.perf_counter()
    host = socket.gethostname() or "local"
    try:
        ram = _ram()
        gpus = _gpus()
        ram_top = _ram_top(15)
        cpu_pct = _cpu_pct()
        return {
            "host": "local",
            "label": host,
            "local": True,
            "ok": True,
            "error": None,
            "gpus": gpus,
            "ram": ram,
            "cpu_pct": cpu_pct,
            "ram_top": ram_top,
            "disk": None,
            "home_disk": None,
            "gpu_count": len(gpus),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "host": "local",
            "label": host,
            "local": True,
            "ok": False,
            "error": str(exc),
            "gpus": [],
            "ram": None,
            "cpu_pct": None,
            "ram_top": [],
            "disk": None,
            "home_disk": None,
            "gpu_count": 0,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
