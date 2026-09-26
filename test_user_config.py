from pathlib import Path

from fastapi.testclient import TestClient

import app
import user_config


def test_user_visibility_and_ip_binding(tmp_path: Path):
    original = user_config._db_path
    try:
        user_config.configure(tmp_path / "users.sqlite3")
        user_config.initialize({
            "aicenter1": [{"ssh_target": "aicenter1"}],
            "h200": [{"ssh_target": "h200"}],
            "legacy": [{"ssh_target": "legacy"}],
        })
        alice = user_config.bind_user("10.0.0.1", "alice")
        user_config.bind_user("10.0.0.3", "bob")
        admin = user_config.bind_user("10.0.0.2", "admin", is_admin=True)
        assert alice == {"username": "alice", "is_admin": False}
        assert admin == {"username": "admin", "is_admin": True}
        assert user_config.user_for_ip("10.0.0.1")["username"] == "alice"

        user_config.add_host("alice_gpu", "alice", [{"ssh_target": "alice_gpu"}], shared=False)
        user_config.add_host("team_gpu", "admin", [{"ssh_target": "team_gpu"}], shared=True)
        alice_hosts = {row["hostname"] for row in user_config.visible_hosts("alice")}
        bob_hosts = {row["hostname"] for row in user_config.visible_hosts("bob")}
        assert "alice_gpu" in alice_hosts and "alice_gpu" not in bob_hosts
        assert "team_gpu" in alice_hosts and "team_gpu" in bob_hosts
        alice_key = user_config.private_host_key("alice", "same_alias")
        bob_key = user_config.private_host_key("bob", "same_alias")
        user_config.add_host(alice_key, "alice", [{"ssh_target": "same_alias"}], shared=False, display_name="same_alias")
        user_config.add_host(bob_key, "bob", [{"ssh_target": "same_alias"}], shared=False, display_name="same_alias")
        assert user_config.visible_host_by_name("alice", "same_alias")["hostname"] == alice_key
        assert user_config.visible_host_by_name("bob", "same_alias")["hostname"] == bob_key
        # A database from an earlier version had private aliases as global
        # keys; initialization migrates them on the next startup.
        user_config.add_host("legacy_private", "alice", [{"ssh_target": "legacy_private"}], shared=False)
        user_config.initialize({})
        migrated = user_config.visible_host_by_name("alice", "legacy_private")
        assert migrated and migrated["hostname"] == user_config.private_host_key("alice", "legacy_private")
        assert user_config.get_host("aicenter1")["visibility"] == "core"
        assert user_config.get_host("h200")["visibility"] == "core"
        assert user_config.get_host("legacy")["visibility"] == "shared"
        assert user_config.rename_host("aicenter1", "Большой GPU") is True
        renamed = user_config.get_host("aicenter1")
        assert renamed["display_name"] == "Большой GPU"
        assert renamed["hostname"] == "aicenter1"
    finally:
        user_config.configure(original)


def test_admin_password_and_ip_session(tmp_path: Path, monkeypatch):
    original = user_config._db_path
    try:
        user_config.configure(tmp_path / "session.sqlite3")
        user_config.initialize({})
        monkeypatch.setenv("GPU_MONITOR_ADMIN_PASSWORD", "0000")
        client = TestClient(app.app)
        assert client.post("/api/session", json={"username": "admin", "password": "bad"}).status_code == 403
        login = client.post("/api/session", json={"username": "admin", "password": "0000"})
        assert login.status_code == 200
        assert login.json()["user"] == {"username": "admin", "is_admin": True}
        assert client.get("/api/session").json()["user"]["username"] == "admin"
        normal = client.post("/api/session", json={"username": "alice"})
        assert normal.status_code == 200
        assert normal.json()["user"] == {"username": "alice", "is_admin": False}
    finally:
        user_config.configure(original)


def test_host_delete_permissions():
    core = {"hostname": "aicenter1", "owner": "admin", "visibility": "core"}
    private = {"hostname": "alice_gpu", "owner": "alice", "visibility": "private"}
    alice = {"username": "alice", "is_admin": False}
    bob = {"username": "bob", "is_admin": False}
    admin = {"username": "admin", "is_admin": True}
    assert not app._can_delete_host(alice, core)
    assert app._can_delete_host(alice, private)
    assert not app._can_delete_host(bob, private)
    assert app._can_delete_host(admin, core)


def test_developer_ui_is_mounted_on_primary_listener(monkeypatch):
    client = TestClient(app.app)

    monkeypatch.setattr(
        user_config, "user_for_ip", lambda _ip: {"username": "alice", "is_admin": False}
    )
    assert client.get("/developer/").status_code == 403
    assert client.get("/developer/api/stats").status_code == 401

    monkeypatch.setattr(
        user_config, "user_for_ip", lambda _ip: {"username": "admin", "is_admin": True}
    )
    page = client.get("/developer/")
    assert page.status_code == 200
    assert "fetch('api/stats')" in page.text
    assert client.get("/developer/api/stats").status_code == 200
    assert client.post("/developer/api/control/restart").status_code == 404


def test_agent_token_accepts_metrics_and_rejects_wrong_token(tmp_path: Path, monkeypatch):
    original_db = user_config._db_path
    original_hosts = list(app.HOSTS)
    try:
        user_config.configure(tmp_path / "agents.sqlite3")
        user_config.initialize({})
        user_config.add_host(
            "h200-agent", "admin", [{"id": "agent-h200-agent", "kind": "agent"}], shared=True
        )
        user_config.set_agent_token("h200-agent", "correct-token")
        assert user_config.agent_hosts()[0]["hostname"] == "h200-agent"
        app.HOSTS[:] = ["h200-agent"]
        app._host_cache.clear()
        monkeypatch.setattr(
            app, "load_host_paths", lambda: {"h200-agent": [{"kind": "agent"}]}
        )
        client = TestClient(app.app)
        probe = """0, NVIDIA H200, 10, 143771, 5, GPU-test
---PROCS---
---USERS---
---RAM---
Mem: 1000 250 0 0 0 750
---DISK---
Filesystem 1B-blocks Used Available Use% Mounted on
/dev/sda 1000 200 800 20% /host
---HOME---
10\t/host/home/test
---ALL_HOMES---
test\t10\t/host/home/test
"""
        assert client.post(
            "/api/agents/h200-agent/metrics", json={"probe_output": probe},
            headers={"X-GPU-Agent-Token": "wrong"},
        ).status_code == 401
        assert client.post(
            "/api/agents/h200-agent/metrics", json={"probe_output": probe},
            headers={"X-GPU-Agent-Token": "correct-token"},
        ).status_code == 200
        state = app._host_cache["h200-agent"]
        assert state["agent"] is True and state["gpus"][0]["name"] == "NVIDIA H200"
        app._mark_stale_agents(state["last_success_at"] + app.AGENT_STALE_SEC + 1)
        assert app._host_cache["h200-agent"]["stale"] is True
    finally:
        app.HOSTS[:] = original_hosts
        app._host_cache.clear()
        user_config.configure(original_db)


def test_probe_parses_inferred_docker_owner():
    probe = """0, NVIDIA A100, 600, 81920, 12, GPU-test
---PROCS---
GPU-test,4242,600,python
---USERS---
4242,docker: ivanov_aa
---RAM---
Mem: 1000 250 0 0 0 750
---DISK---
Filesystem 1B-blocks Used Available Use% Mounted on
/dev/sda 1000 200 800 20% /data
---HOME---
10\t/data/homes/test
---ALL_HOMES---
"""
    result = app._parse_output("test", probe, "", 0)
    assert result["ok"] is True
    assert result["gpus"][0]["processes"][0]["user"] == "docker: ivanov_aa"
    assert result["gpus"][0]["users"] == [{"user": "docker: ivanov_aa", "mem_mib": 600.0, "mem_gib": 0.59}]


def test_existing_database_gets_display_name_migration(tmp_path: Path):
    import sqlite3

    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE users (username TEXT PRIMARY KEY COLLATE NOCASE, is_admin INTEGER NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE ip_bindings (ip TEXT PRIMARY KEY, username TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE hosts (hostname TEXT PRIMARY KEY COLLATE NOCASE, owner TEXT, visibility TEXT NOT NULL, paths_json TEXT NOT NULL, created_at REAL NOT NULL);
        """)
    original = user_config._db_path
    try:
        user_config.configure(database)
        user_config.initialize({})
        with sqlite3.connect(database) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(hosts)")}
        assert "display_name" in columns
    finally:
        user_config.configure(original)


def test_only_admin_can_rename_host(tmp_path: Path, monkeypatch):
    original = user_config._db_path
    try:
        user_config.configure(tmp_path / "rename.sqlite3")
        user_config.initialize({"h200": [{"ssh_target": "h200"}]})
        monkeypatch.setenv("GPU_MONITOR_ADMIN_PASSWORD", "0000")
        client = TestClient(app.app)
        assert client.post("/api/session", json={"username": "alice"}).status_code == 200
        denied = client.patch("/api/hosts/h200", json={"display_name": "H200 Lab"})
        assert denied.status_code == 403
        assert client.post(
            "/api/session", json={"username": "admin", "password": "0000"}
        ).status_code == 200
        renamed = client.patch("/api/hosts/h200", json={"display_name": "H200 Lab"})
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "H200 Lab"
        assert user_config.get_host("h200")["hostname"] == "h200"
    finally:
        user_config.configure(original)
