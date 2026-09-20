"""Regression tests for the audit's concurrency, cache, security and UI fixes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import app
import gpu_metrics_history
import host_paths
import ssh_runtime


def result(host: str, ok: bool = True, marker: str = "new") -> dict:
    return {
        "host": host,
        "ok": ok,
        "reachable": ok,
        "error": None if ok else marker,
        "gpus": [{"index": 0, "name": marker, "mem_used_mib": 1, "mem_total_mib": 2}],
        "ram": {"used_bytes": 1, "total_bytes": 2},
        "paths": [],
        "latency_ms": 1,
    }


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app, "LAST_GOOD_DIR", tmp_path / "last_good")
    old_hosts = list(app.HOSTS)
    app.HOSTS[:] = ["a", "b"]
    app._host_cache.clear()
    app._host_generation.clear()
    app._host_generation.update({"a": 1, "b": 1})
    app._host_next_due.clear()
    app._host_failures.clear()
    app._host_tasks.clear()
    app._cache.update({"ts": 0.0, "servers": [], "local": None, "local_ts": 0.0, "gen": 0})
    app._probe_sem = asyncio.Semaphore(3)
    yield
    for task in list(app._host_tasks.values()):
        task.cancel()
    app._host_tasks.clear()
    app.HOSTS[:] = old_hosts
    app._host_cache.clear()


def test_failed_probe_preserves_last_successful_payload():
    assert app._apply_probe_result("a", 1, result("a", marker="good"))
    last_success = app._host_cache["a"]["last_success_at"]
    assert app._apply_probe_result("a", 1, result("a", ok=False, marker="timeout"))
    state = app._host_cache["a"]
    assert state["gpus"][0]["name"] == "good"
    assert state["stale"] is True
    assert state["connection_ok"] is False
    assert state["error"] == "timeout"
    assert state["last_success_at"] == last_success


def test_delete_add_same_name_rejects_old_generation():
    app._host_generation["a"] = 2  # DELETE then ADD creates a new identity
    assert not app._apply_probe_result("a", 1, result("a", marker="old"))
    assert app._apply_probe_result("a", 2, result("a", marker="new"))
    assert app._host_cache["a"]["gpus"][0]["name"] == "new"


@pytest.mark.asyncio
async def test_metrics_requests_never_launch_remote_probe():
    app._host_cache.update({"a": result("a"), "b": result("b")})
    app._cache["ts"] = time.time() - 999
    request = Request({"type": "http", "method": "GET", "path": "/api/metrics",
                       "headers": [], "client": ("127.0.0.1", 1234)})
    visible = [{"hostname": h, "owner": "tester", "visibility": "private"} for h in ("a", "b")]
    with patch("app._probe_host") as probe, patch("app.zerotier_networks", return_value=[]), \
         patch("app._refresh_local", return_value=None), \
         patch("app.user_config.user_for_ip", return_value={"username": "tester", "is_admin": False}), \
         patch("app.user_config.visible_hosts", return_value=visible):
        replies = await asyncio.gather(*(app.metrics(request) for _ in range(8)))
    assert all(len(reply["servers"]) >= 2 for reply in replies)
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_slow_host_does_not_block_fast_host_commit():
    fast_committed = asyncio.Event()

    async def fake_probe(host: str):
        if host == "a":
            await asyncio.sleep(0.15)
        return result(host)

    original_apply = app._apply_probe_result

    def observing_apply(host, generation, payload):
        committed = original_apply(host, generation, payload)
        if host == "b" and committed:
            fast_committed.set()
        return committed

    with patch("app._probe_host", side_effect=fake_probe), patch("app._apply_probe_result", side_effect=observing_apply):
        slow = asyncio.create_task(app._run_host_probe("a", 1))
        fast = asyncio.create_task(app._run_host_probe("b", 1))
        await asyncio.wait_for(fast_committed.wait(), 0.08)
        assert not slow.done()
        await asyncio.gather(slow, fast)


def test_all_route_ssh_processes_obey_shared_limit():
    ssh_runtime.reset_for_tests()

    class Completed:
        returncode = 0
        stdout = "OK\n"
        stderr = ""

    def slow_run(*_args, **_kwargs):
        time.sleep(0.03)
        return Completed()

    with patch("host_paths.subprocess.run", side_effect=slow_run):
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(host_paths._ssh_ok, f"h{i}") for i in range(12)]
            assert all(f.result()[0] for f in futures)
    snap = ssh_runtime.snapshot()
    assert snap["peak_active"] <= snap["max_active_limit"] == 3
    assert snap["connections_last_minute"] == 12


@pytest.mark.asyncio
async def test_history_record_batch_really_persists(tmp_path: Path):
    db = tmp_path / "history.db"
    with patch.object(gpu_metrics_history, "DB_PATH", db):
        gpu_metrics_history.init_db()
        gpu_metrics_history.record_batch("a", result("a")["gpus"])
        rows = gpu_metrics_history.get_history("a", 0, 1)
    assert len(rows) == 1
    assert rows[0]["vram_used_mib"] == 1


@pytest.mark.asyncio
async def test_remote_non_admin_is_forbidden(monkeypatch):
    monkeypatch.delenv("GPU_MONITOR_ADMIN_TOKEN", raising=False)
    request = Request({"type": "http", "method": "POST", "path": "/api/hosts", "headers": [], "client": ("10.0.0.9", 1234)})
    with pytest.raises(HTTPException) as exc:
        await app.require_admin(request)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_remote_admin_token_is_accepted(monkeypatch):
    monkeypatch.setenv("GPU_MONITOR_ADMIN_TOKEN", "secret-value")
    request = Request({
        "type": "http", "method": "POST", "path": "/api/hosts",
        "headers": [(b"x-admin-token", b"secret-value")], "client": ("10.0.0.9", 1234),
    })
    assert await app.require_admin(request) is None


def test_frontend_serializes_polling_and_guards_mutations():
    source = (Path(app.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
    html = (Path(app.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "if (_tickRunning) return" in source
    assert "mutationVersion !== _serverMutationVersion" in source
    assert "ctrl.abort()" in source
    assert "grid.innerHTML = servers.map" not in source
    assert "retainLastKnownMetrics" in source
    assert 'grid.querySelector(".card-settings-menu.open")' in source
    assert "grid.appendChild(card);" not in source
    assert "window.scrollTo(viewportX, viewportY)" in source
    assert "locallyAddedHosts" in source
    assert "locallyDeletedHosts" in source
    assert "persistLocalHostMutations" in source
    assert "LAST_HOST_ERRORS_KEY" in source
    assert "isLoadingPlaceholder(next) && previousConcreteError" in source
    assert "persistLastHostErrors()" in source
    assert "showAdminPasswordStep" in source
    assert "userLoginBack" in source
    assert ".user-login-fields [hidden] { display: none !important; }" in html
    assert 'data-action="rename"' in source
    assert 'method: "PATCH"' in source


def test_ssh_diagnostics_contains_required_counters():
    snap = ssh_runtime.snapshot()
    required = {
        "active", "peak_active", "connections_last_minute", "attempts_by_host",
        "timeouts_by_host", "errors_by_host", "average_wait_ms",
        "average_duration_ms", "route_diagnostics",
    }
    assert required <= snap.keys()


def test_host_config_write_is_atomic(tmp_path: Path):
    target = tmp_path / "host_paths.json"
    with patch.object(app, "PATHS_FILE", target):
        app._write_host_paths_atomic({"safe": [{"ssh_target": "safe"}]})
    assert json.loads(target.read_text(encoding="utf-8"))["safe"][0]["ssh_target"] == "safe"
    assert not target.with_suffix(".json.tmp").exists()


def test_real_fastapi_metrics_handler_is_cache_only():
    app._host_cache.update({"a": result("a"), "b": result("b")})
    app._cache["ts"] = time.time()
    visible = [{"hostname": h, "owner": "tester", "visibility": "private"} for h in ("a", "b")]
    with patch("app._probe_host") as probe, \
         patch("app.user_config.user_for_ip", return_value={"username": "tester", "is_admin": False}), \
         patch("app.user_config.visible_hosts", return_value=visible):
        response = TestClient(app.app).get("/api/metrics")
    assert response.status_code == 200
    assert {row["host"] for row in response.json()["servers"]} >= {"a", "b"}
    probe.assert_not_called()


def test_real_fastapi_add_handler_probes_only_new_host():
    launched: list[str] = []
    with patch("app.load_host_paths", return_value={}), \
         patch("app._write_host_paths_atomic"), \
         patch("app.user_config.user_for_ip", return_value={"username": "tester", "is_admin": False}), \
         patch("app.user_config.get_host", return_value=None), \
         patch("app.user_config.add_host"), \
         patch("app.user_config.all_hosts", return_value={}), \
         patch("app._launch_host_probe", side_effect=lambda host, **_kw: launched.append(host)):
        response = TestClient(app.app).post(
            "/api/hosts", json={"hostname": "new_host", "ip": "10.1.2.3", "port": 22}
        )
    assert response.status_code == 200 and response.json()["ok"] is True
    assert launched == ["new_host"]
    assert "new_host" in app._host_cache


@pytest.mark.asyncio
async def test_duplicate_startup_does_not_duplicate_collectors():
    app._lifecycle_started = False
    calls = 0

    def consume(coro):
        nonlocal calls
        calls += 1
        coro.close()
        return None

    with patch("app._track_task", side_effect=consume):
        await app._startup_probe_local()
        first = calls
        await app._startup_probe_local()
    app._lifecycle_started = False
    assert first == calls == 5  # local, ZT, scheduler, quotas, history


def test_last_good_cache_survives_backend_restart(tmp_path: Path):
    snapshot = {
        **result("a", marker="persisted"),
        "connection_ok": True,
        "stale": False,
        "last_success_at": 1234.0,
    }
    with patch.object(app, "LAST_GOOD_DIR", tmp_path):
        app._persist_last_good("a", snapshot)
        app._host_cache.clear()
        app._load_last_good_cache()
    restored = app._host_cache["a"]
    assert restored["gpus"][0]["name"] == "persisted"
    assert restored["ok"] is True
    assert restored["stale"] is True
    assert restored["connection_ok"] is False
    assert "загрузка" not in restored["error"]
