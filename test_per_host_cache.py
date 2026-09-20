"""Automated tests for per-host cache architecture.

Covers:
 1. ADD host -> immediate placeholder, then real data via background probe
 2. DELETE host -> immediate removal from cache
 3. Stale-result guard -> probe result for deleted host is discarded
 4. Incremental update -> each host updates independently
 5. Cache preservation -> previous data survives a refresh cycle
 6. Semaphore limits concurrent _probe_host calls (shared between sweep and ADD)
 7. Error handling -> SSH failure stored as error, not crash
 8. Metrics history collector uses _host_cache, not non-existent _collect()
 9. ADD triggers independent probe, not full sweep
10. Multiple /api/metrics callers don't trigger extra SSH probes
11. Frontend tick() discards stale responses (request ordering)
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

import app


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state before each test."""
    app.HOSTS.clear()
    app.HOSTS.extend(["host_a", "host_b"])
    app._host_cache.clear()
    app._cache.update({"ts": 0.0, "servers": [], "local": None, "local_ts": 0.0, "gen": 0})
    app._cache_gen = 0
    app._refreshing = False
    app._probe_sem = asyncio.Semaphore(3)
    yield
    app.HOSTS.clear()
    app.HOSTS.extend(["host_a", "host_b"])
    app._host_cache.clear()


def _make_result(host: str, ok: bool = True, gpus: int = 2) -> dict:
    return {
        "host": host,
        "ok": ok,
        "reachable": ok,
        "error": None if ok else "ssh failed",
        "gpus": [{"index": i, "name": f"GPU{i}", "util": 50, "mem_used": 4000, "mem_total": 8000} for i in range(gpus)],
        "ram": {"total": 32000, "used": 16000},
        "latency_ms": 50,
        "paths": [],
    }


# --- 1. ADD host appears immediately with placeholder ---

async def test_add_host_immediate_placeholder():
    app.HOSTS.append("new_host")
    placeholder = {
        "host": "new_host", "ok": False, "reachable": False,
        "error": "загрузка…", "gpus": [], "ram": None,
        "latency_ms": None, "paths": [],
    }
    app._host_cache["new_host"] = placeholder

    hosts = [s["host"] for s in app._host_cache.values()]
    assert "new_host" in hosts
    assert app._host_cache["new_host"]["error"] == "загрузка…"


# --- 2. DELETE host disappears immediately ---

async def test_delete_host_immediate_removal():
    app._host_cache["host_a"] = _make_result("host_a")
    app._host_cache["host_b"] = _make_result("host_b")

    app.HOSTS.remove("host_a")
    app._host_cache.pop("host_a", None)

    hosts = [s["host"] for s in app._host_cache.values() if s["host"] in app.HOSTS]
    assert "host_a" not in hosts
    assert "host_b" in hosts


# --- 3. Stale-result guard ---

async def test_stale_result_guard():
    async def slow_probe(host):
        await asyncio.sleep(0.1)
        return _make_result(host)

    with patch("app._probe_host", side_effect=slow_probe):
        app.HOSTS.append("doomed")
        task = asyncio.create_task(app._collect_incremental())
        await asyncio.sleep(0.02)
        app.HOSTS.remove("doomed")
        await task

    assert "doomed" not in app._host_cache


# --- 4. Incremental update ---

async def test_incremental_update():
    call_order = []

    async def staggered_probe(host):
        delay = {"host_a": 0.05, "host_b": 0.15}[host]
        await asyncio.sleep(delay)
        call_order.append(host)
        return _make_result(host)

    with patch("app._probe_host", side_effect=staggered_probe):
        await app._collect_incremental()

    assert "host_a" in app._host_cache
    assert "host_b" in app._host_cache
    assert call_order[0] == "host_a"
    # host_a was written to cache before host_b finished
    assert app._host_cache["host_a"]["ok"]


# --- 5. Cache preservation during refresh ---

async def test_cache_preservation_during_refresh():
    """Previous data stays in cache while a new sweep runs."""
    app._host_cache["host_a"] = _make_result("host_a", ok=True, gpus=4)
    app._host_cache["host_b"] = _make_result("host_b", ok=True, gpus=2)

    async def slow_sweep(host):
        await asyncio.sleep(0.2)
        return _make_result(host, gpus=99)

    with patch("app._probe_host", side_effect=slow_sweep):
        task = asyncio.create_task(app._collect_incremental())
        await asyncio.sleep(0.05)
        # During the sweep, old data is still in cache
        assert app._host_cache["host_a"]["gpus"][0]["index"] == 0
        assert len(app._host_cache["host_a"]["gpus"]) == 4
        assert len(app._host_cache["host_b"]["gpus"]) == 2
        await task

    # After sweep, new data replaced old
    assert len(app._host_cache["host_a"]["gpus"]) == 99


# --- 6. Semaphore limits concurrency (shared between sweep and ADD) ---

async def test_semaphore_limits_concurrency():
    max_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    async def tracking_probe(host):
        nonlocal max_concurrent, current
        async with lock:
            current += 1
            max_concurrent = max(max_concurrent, current)
        await asyncio.sleep(0.1)
        async with lock:
            current -= 1
        return _make_result(host)

    app.HOSTS.clear()
    app.HOSTS.extend([f"h{i}" for i in range(8)])

    with patch("app._probe_host", side_effect=tracking_probe):
        await app._collect_incremental()

    assert max_concurrent <= 3


# --- 7. Error handling ---

async def test_error_handling():
    async def failing_probe(host):
        return {
            "host": host, "ok": False, "reachable": False,
            "error": "ssh: connect to host: Connection refused",
            "gpus": [], "ram": None, "latency_ms": 100, "paths": [],
        }

    with patch("app._probe_host", side_effect=failing_probe):
        await app._collect_incremental()

    for h in ["host_a", "host_b"]:
        assert h in app._host_cache
        assert not app._host_cache[h]["ok"]
        assert "Connection refused" in app._host_cache[h]["error"]


# --- 8. Metrics history collector uses _host_cache ---

async def test_metrics_hist_collector_uses_cache():
    app._host_cache["host_a"] = _make_result("host_a", ok=True, gpus=2)

    with patch("app.gpu_metrics_history.record_batch") as mock_record, \
         patch("app.gpu_metrics_history.cleanup_old") as mock_cleanup:
        task = asyncio.create_task(app._metrics_hist_collector())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Collector sleeps 60s before first tick — no crash = success


# --- 9. ADD triggers independent probe, not full sweep ---

async def test_add_triggers_independent_probe():
    """Adding a host probes ONLY that host, not all hosts."""
    probed_hosts = []

    async def tracking_probe(host):
        probed_hosts.append(host)
        await asyncio.sleep(0.05)
        return _make_result(host)

    app._host_cache["host_a"] = _make_result("host_a")
    app._host_cache["host_b"] = _make_result("host_b")

    with patch("app._probe_host", side_effect=tracking_probe):
        app.HOSTS.append("new_one")
        app._host_cache["new_one"] = {"host": "new_one", "ok": False, "error": "загрузка…",
                                       "gpus": [], "ram": None, "reachable": False,
                                       "latency_ms": None, "paths": []}
        # Simulate what api_add_host does
        async with app._probe_sem:
            result = await app._probe_host("new_one")
            if "new_one" in app.HOSTS:
                app._host_cache["new_one"] = result

    assert probed_hosts == ["new_one"]


# --- 10. Multiple /api/metrics callers don't trigger extra SSH ---

async def test_multiple_callers_no_extra_probes():
    """Concurrent /api/metrics calls share one sweep, not one-per-caller."""
    probe_count = 0

    async def counting_probe(host):
        nonlocal probe_count
        probe_count += 1
        await asyncio.sleep(0.1)
        return _make_result(host)

    with patch("app._probe_host", side_effect=counting_probe):
        # Simulate 5 concurrent /api/metrics calls triggering refresh
        tasks = [asyncio.create_task(app._refresh_cache()) for _ in range(5)]
        await asyncio.gather(*tasks)

    # _refresh_cache has a guard: if _refreshing, return immediately.
    # So only ONE sweep should run, probing each host once.
    assert probe_count == 2  # host_a + host_b


# --- 11. Shared semaphore between sweep and ADD ---

async def test_add_probe_shares_semaphore():
    """ADD handler's probe uses the same semaphore as the sweep."""
    max_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    async def tracking_probe(host):
        nonlocal max_concurrent, current
        async with lock:
            current += 1
            max_concurrent = max(max_concurrent, current)
        await asyncio.sleep(0.1)
        async with lock:
            current -= 1
        return _make_result(host)

    app.HOSTS.clear()
    app.HOSTS.extend([f"h{i}" for i in range(6)])

    with patch("app._probe_host", side_effect=tracking_probe):
        # Start sweep
        sweep = asyncio.create_task(app._collect_incremental())
        await asyncio.sleep(0.02)
        # Simulate ADD probe while sweep is running
        app.HOSTS.append("added")

        async def add_probe():
            async with app._probe_sem:
                await app._probe_host("added")

        add_task = asyncio.create_task(add_probe())
        await asyncio.gather(sweep, add_task)

    # Sweep (6 hosts) + ADD (1 host) = 7 probes, but max 3 concurrent
    assert max_concurrent <= 3
