"""SQLite-backed user identity and per-user host configuration."""

from __future__ import annotations

import json
import hashlib
import hmac
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
            CREATE TABLE IF NOT EXISTS agent_tokens (
                hostname TEXT PRIMARY KEY COLLATE NOCASE REFERENCES hosts(hostname) ON DELETE CASCADE,
                token_digest TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY COLLATE NOCASE,
                token_digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen_at REAL
            );
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(hosts)")}
        if "display_name" not in columns:
            db.execute("ALTER TABLE hosts ADD COLUMN display_name TEXT")
        # Agent registration used to be tied to a host row. Preserve all
        # legacy registrations, but keep future agents independent so deleting
        # a card cannot revoke a running agent.
        db.execute(
            """INSERT OR IGNORE INTO agents(agent_id,token_digest,created_at)
               SELECT hostname,token_digest,created_at FROM agent_tokens"""
        )
        # Private hosts created before per-user identities used their visible
        # alias as the global primary key. Move them once so they no longer
        # prevent another user from adding the same SSH alias.
        legacy_private = db.execute(
            "SELECT hostname,owner,display_name FROM hosts WHERE visibility='private'"
        ).fetchall()
        for row in legacy_private:
            hostname, owner = str(row["hostname"]), str(row["owner"] or "")
            if not owner or hostname.startswith("private--"):
                continue
            internal = private_host_key(owner, hostname)
            if db.execute("SELECT 1 FROM hosts WHERE hostname=?", (internal,)).fetchone():
                continue
            db.execute(
                "UPDATE hosts SET hostname=?,display_name=COALESCE(display_name,?) WHERE hostname=?",
                (internal, hostname, hostname),
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
            """SELECT hostname,owner,visibility,display_name FROM hosts
               WHERE visibility IN ('core','shared') OR owner=? COLLATE NOCASE
               ORDER BY created_at,hostname""", (username,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_host(hostname: str) -> dict[str, Any] | None:
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT hostname,owner,visibility,display_name,paths_json FROM hosts WHERE hostname=? COLLATE NOCASE",
            (hostname,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["paths"] = json.loads(result.pop("paths_json"))
    return result


def private_host_key(owner: str, hostname: str) -> str:
    """Stable internal key; the displayed card name remains ``hostname``."""
    digest = hashlib.sha256(owner.casefold().encode("utf-8")).hexdigest()[:12]
    return f"private--{digest}--{hostname}"


def visible_host_by_name(username: str, hostname: str) -> dict[str, Any] | None:
    """Find a host already usable by this user, including their private copy."""
    with _lock, _connect() as db:
        row = db.execute(
            """SELECT hostname,owner,visibility,display_name,paths_json FROM hosts
               WHERE (visibility IN ('core','shared') OR owner=? COLLATE NOCASE)
                 AND (hostname=? COLLATE NOCASE OR display_name=? COLLATE NOCASE)
               ORDER BY CASE visibility WHEN 'core' THEN 0 WHEN 'shared' THEN 1 ELSE 2 END
               LIMIT 1""",
            (username, hostname, hostname),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["paths"] = json.loads(result.pop("paths_json"))
    return result


def add_host(
    hostname: str, owner: str, paths: list[dict[str, Any]], *, shared: bool,
    display_name: str | None = None,
) -> None:
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO hosts(hostname,owner,visibility,paths_json,created_at,display_name) VALUES(?,?,?,?,?,?)",
            (hostname, owner, "shared" if shared else "private",
             json.dumps(paths, ensure_ascii=False), time.time(), display_name),
        )


def delete_host(hostname: str) -> None:
    with _lock, _connect() as db:
        db.execute("DELETE FROM hosts WHERE hostname=? COLLATE NOCASE", (hostname,))


def rename_host(hostname: str, display_name: str | None) -> bool:
    with _lock, _connect() as db:
        cursor = db.execute(
            "UPDATE hosts SET display_name=? WHERE hostname=? COLLATE NOCASE",
            (display_name, hostname),
        )
        return cursor.rowcount == 1


def set_agent_token(hostname: str, token: str) -> None:
    """Register an agent independently from any visible host card."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _lock, _connect() as db:
        db.execute(
            """INSERT INTO agents(agent_id,token_digest,created_at) VALUES(?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 token_digest=excluded.token_digest, created_at=excluded.created_at""",
            (hostname, digest, time.time()),
        )


def agent_token_valid(hostname: str, token: str) -> bool:
    if not token:
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT token_digest FROM agents WHERE agent_id=? COLLATE NOCASE",
            (hostname,),
        ).fetchone()
    return bool(row and hmac.compare_digest(str(row["token_digest"]), digest))


def agent_hosts() -> list[dict[str, Any]]:
    """Registered push agents, without exposing their credentials."""
    with _lock, _connect() as db:
        rows = db.execute(
            """SELECT agent_id AS hostname,agent_id AS display_name,created_at,last_seen_at
               FROM agents ORDER BY created_at,agent_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def agent_exists(agent_id: str) -> bool:
    with _lock, _connect() as db:
        return bool(db.execute("SELECT 1 FROM agents WHERE agent_id=? COLLATE NOCASE", (agent_id,)).fetchone())


def touch_agent(agent_id: str, timestamp: float) -> None:
    with _lock, _connect() as db:
        db.execute("UPDATE agents SET last_seen_at=? WHERE agent_id=? COLLATE NOCASE", (timestamp, agent_id))
