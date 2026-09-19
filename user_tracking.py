"""User tracking database for GPU profiler."""

import sqlite3
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "users.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                first_visit REAL NOT NULL,
                last_visit REAL NOT NULL,
                visit_count INTEGER NOT NULL DEFAULT 1,
                settings TEXT DEFAULT '{}'
            )
        """)
        conn.commit()
    finally:
        conn.close()


def track_visit(username: str) -> dict[str, Any]:
    if not username or not username.strip():
        return {"error": "username required"}

    username = username.strip()
    now = time.time()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, first_visit, visit_count FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if row:
            conn.execute(
                "UPDATE users SET last_visit = ?, visit_count = ? WHERE id = ?",
                (now, row["visit_count"] + 1, row["id"])
            )
            conn.commit()
            return {
                "username": username,
                "first_visit": row["first_visit"],
                "last_visit": now,
                "visit_count": row["visit_count"] + 1,
                "is_new": False,
            }
        else:
            conn.execute(
                "INSERT INTO users (username, first_visit, last_visit, visit_count) VALUES (?, ?, ?, 1)",
                (username, now, now)
            )
            conn.commit()
            return {
                "username": username,
                "first_visit": now,
                "last_visit": now,
                "visit_count": 1,
                "is_new": True,
            }
    finally:
        conn.close()


def get_user(username: str) -> dict[str, Any] | None:
    if not username:
        return None
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT username, first_visit, last_visit, visit_count, settings FROM users WHERE username = ?",
            (username.strip(),)
        ).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def get_all_users() -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT username, first_visit, last_visit, visit_count FROM users ORDER BY last_visit DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_settings(username: str, settings: dict[str, Any]) -> bool:
    import json
    if not username:
        return False
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET settings = ? WHERE username = ?",
            (json.dumps(settings), username.strip())
        )
        conn.commit()
        return True
    finally:
        conn.close()


init_db()
