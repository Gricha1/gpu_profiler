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
        assert user_config.get_host("aicenter1")["visibility"] == "core"
        assert user_config.get_host("h200")["visibility"] == "core"
        assert user_config.get_host("legacy")["visibility"] == "shared"
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
