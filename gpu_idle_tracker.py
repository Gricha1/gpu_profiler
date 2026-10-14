"""Persistent per-GPU idle timers.

A GPU is considered active while it has a compute process or reports meaningful
utilisation. The timer is deliberately updated only from a successful live
probe, so an SSH/agent outage never turns an unknown GPU into an "idle" one.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parent / "data" / "metrics" / "gpu_idle.db"


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_idle_state (
                host TEXT NOT NULL,
                gpu_key TEXT NOT NULL,
                gpu_index INTEGER NOT NULL,
                idle_since REAL,
                last_observed REAL NOT NULL,
                PRIMARY KEY (host, gpu_key)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _is_active(gpu: dict[str, Any]) -> bool:
    """Return whether the current probe shows work running on this GPU."""
    if gpu.get("processes"):
        return True
    try:
        return float(gpu.get("util_pct") or 0) > 1.0
    except (TypeError, ValueError):
        return False


def annotate_idle(
    host: str,
    gpus: list[dict[str, Any]],
    observed_at: float | None = None,
    *,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Add ``idle_since`` and ``idle_seconds`` to a fresh GPU snapshot.

    ``gpus`` is updated in place and returned for convenience. A GPU first
    seen idle starts its timer from that observation; we never infer a longer
    duration from missing historical process data.
    """
    now = time.time() if observed_at is None else float(observed_at)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_idle_state (
                host TEXT NOT NULL, gpu_key TEXT NOT NULL, gpu_index INTEGER NOT NULL,
                idle_since REAL, last_observed REAL NOT NULL,
                PRIMARY KEY (host, gpu_key)
            )
            """
        )
        for gpu in gpus:
            index = int(gpu.get("index") or 0)
            gpu_key = str(gpu.get("uuid") or f"index:{index}")
            row = conn.execute(
                "SELECT idle_since FROM gpu_idle_state WHERE host = ? AND gpu_key = ?",
                (host, gpu_key),
            ).fetchone()
            if _is_active(gpu):
                idle_since = None
            elif row is not None and row["idle_since"] is not None:
                idle_since = float(row["idle_since"])
            else:
                idle_since = now

            conn.execute(
                """
                INSERT INTO gpu_idle_state (host, gpu_key, gpu_index, idle_since, last_observed)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(host, gpu_key) DO UPDATE SET
                    gpu_index = excluded.gpu_index,
                    idle_since = excluded.idle_since,
                    last_observed = excluded.last_observed
                """,
                (host, gpu_key, index, idle_since, now),
            )
            gpu["idle_since"] = idle_since
            gpu["idle_seconds"] = max(0, round(now - idle_since)) if idle_since is not None else 0
            gpu["idle_tracking"] = True
        conn.commit()
    finally:
        conn.close()
    return gpus


init_db()
