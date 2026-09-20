"""GPU metrics history storage."""

import sqlite3
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "data" / "metrics" / "gpu_history.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

RETENTION_SEC = 14 * 24 * 3600


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gpu_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                host TEXT NOT NULL,
                gpu_index INTEGER NOT NULL,
                vram_used_mib REAL,
                vram_total_mib REAL,
                vram_pct REAL,
                util_pct REAL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gpu_metrics_lookup
            ON gpu_metrics(host, gpu_index, timestamp)
        """)
        conn.commit()
    finally:
        conn.close()


def record_metrics(host: str, gpu_index: int, vram_used_mib: float | None,
                    vram_total_mib: float | None, vram_pct: float | None,
                    util_pct: float | None) -> None:
    now = time.time()
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO gpu_metrics
               (timestamp, host, gpu_index, vram_used_mib, vram_total_mib, vram_pct, util_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now, host, gpu_index, vram_used_mib, vram_total_mib, vram_pct, util_pct)
        )
        conn.commit()
    finally:
        conn.close()


def record_batch(host: str, gpus: list[dict[str, Any]]) -> None:
    now = time.time()
    conn = _get_conn()
    try:
        rows = []
        for g in gpus:
            rows.append((
                now, host, g.get("index", 0),
                g.get("mem_used_mib"), g.get("mem_total_mib"),
                g.get("mem_pct"), g.get("util_pct")
            ))
        conn.executemany(
            """INSERT INTO gpu_metrics
               (timestamp, host, gpu_index, vram_used_mib, vram_total_mib, vram_pct, util_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows
        )
        conn.commit()
    finally:
        conn.close()


def get_history(host: str, gpu_index: int, hours: int = 24) -> list[dict[str, Any]]:
    since = time.time() - (hours * 3600)
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT timestamp, vram_used_mib, vram_total_mib, vram_pct, util_pct
               FROM gpu_metrics
               WHERE host = ? AND gpu_index = ? AND timestamp >= ?
               ORDER BY timestamp ASC""",
            (host, gpu_index, since)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_history(host: str, hours: int = 24) -> dict[int, list[dict[str, Any]]]:
    """Return history for all GPUs of a host, keyed by gpu_index."""
    since = time.time() - (hours * 3600)
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT gpu_index, timestamp, vram_used_mib, vram_total_mib, vram_pct, util_pct
               FROM gpu_metrics
               WHERE host = ? AND timestamp >= ?
               ORDER BY gpu_index, timestamp ASC""",
            (host, since)
        ).fetchall()
        result = {}
        for r in rows:
            idx = r["gpu_index"]
            if idx not in result:
                result[idx] = []
            result[idx].append({
                "timestamp": r["timestamp"],
                "vram_used_mib": r["vram_used_mib"],
                "vram_total_mib": r["vram_total_mib"],
                "vram_pct": r["vram_pct"],
                "util_pct": r["util_pct"]
            })
        return result
    finally:
        conn.close()


def cleanup_old() -> int:
    cutoff = time.time() - RETENTION_SEC
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM gpu_metrics WHERE timestamp < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


init_db()
