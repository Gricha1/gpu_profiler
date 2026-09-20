"""SQLite-backed user identity and per-user host configuration."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


_lock = threading.RLock()
_db_path = Path(__file__).resolve().parent / "data" / "users.sqlite3"


def configure(path: Path) -> None:
    global _db_path
    _db_path = path


def _connect() -> sqlite3.Connection:
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize(initial_hosts: dict[str, list[dict[str, Any]]]) -> None:
    with _lock, _connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ip_bindings (
                ip TEXT PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE REFERENCES users(username),
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hosts (
                hostname TEXT PRIMARY KEY COLLATE NOCASE,
                owner TEXT COLLATE NOCASE REFERENCES users(username),
                visibility TEXT NOT NULL CHECK (visibility IN ('core','shared','private')),
                paths_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hosts_owner ON hosts(owner);
            """
        )
        now = time.time()
        db.execute(
            "INSERT OR IGNORE INTO users(username,is_admin,created_at) VALUES('admin',1,?)",
            (now,),
        )
        for hostname, paths in initial_hosts.items():
            core = hostname == "h200" or (
                hostname.startswith("aicenter")
                and (hostname[len("aicenter"):].isdigit() or hostname == "aicenteritl")
            )
            db.execute(
                """INSERT OR IGNORE INTO hosts
                   (hostname,owner,visibility,paths_json,created_at) VALUES(?,?,?,?,?)""",
                (hostname, "admin", "core" if core else "shared",
                 json.dumps(paths, ensure_ascii=False), now),
            )


def bind_user(ip: str, username: str, *, is_admin: bool = False) -> dict[str, Any]:
    username = username.strip()
    now = time.time()
    with _lock, _connect() as db:
        existing = db.execute(
            "SELECT username,is_admin FROM users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()
        if existing:
            canonical = str(existing["username"])
            admin = bool(existing["is_admin"])
        else:
            canonical = username
            admin = bool(is_admin)
            db.execute(
                "INSERT INTO users(username,is_admin,created_at) VALUES(?,?,?)",
                (canonical, int(admin), now),
            )
        db.execute(
            """INSERT INTO ip_bindings(ip,username,updated_at) VALUES(?,?,?)
               ON CONFLICT(ip) DO UPDATE SET username=excluded.username,updated_at=excluded.updated_at""",
            (ip, canonical, now),
        )
    return {"username": canonical, "is_admin": admin}


def user_for_ip(ip: str) -> dict[str, Any] | None:
    with _lock, _connect() as db:
        row = db.execute(
            """SELECT u.username,u.is_admin FROM ip_bindings b
               JOIN users u ON u.username=b.username WHERE b.ip=?""", (ip,)
        ).fetchone()
    return {"username": row["username"], "is_admin": bool(row["is_admin"])} if row else None


def all_hosts() -> dict[str, list[dict[str, Any]]]:
    with _lock, _connect() as db:
        rows = db.execute("SELECT hostname,paths_json FROM hosts ORDER BY created_at,hostname").fetchall()
    return {str(row["hostname"]): json.loads(row["paths_json"]) for row in rows}


def visible_hosts(username: str) -> list[dict[str, Any]]:
    with _lock, _connect() as db:
        rows = db.execute(
            """SELECT hostname,owner,visibility FROM hosts
               WHERE visibility IN ('core','shared') OR owner=? COLLATE NOCASE
               ORDER BY created_at,hostname""", (username,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_host(hostname: str) -> dict[str, Any] | None:
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT hostname,owner,visibility,paths_json FROM hosts WHERE hostname=? COLLATE NOCASE",
            (hostname,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["paths"] = json.loads(result.pop("paths_json"))
    return result


def add_host(hostname: str, owner: str, paths: list[dict[str, Any]], *, shared: bool) -> None:
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO hosts(hostname,owner,visibility,paths_json,created_at) VALUES(?,?,?,?,?)",
            (hostname, owner, "shared" if shared else "private",
             json.dumps(paths, ensure_ascii=False), time.time()),
        )


def delete_host(hostname: str) -> None:
    with _lock, _connect() as db:
        db.execute("DELETE FROM hosts WHERE hostname=? COLLATE NOCASE", (hostname,))

