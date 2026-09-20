# AGENTS.md — canonical technical memory for GPU Profiler / gpu_monitor

> **CURRENT STATE.** This is the only authoritative architecture document for
> this project. Every Cursor / Codex / other AI-coding agent working here must
> read this first and treat anything that contradicts it as wrong.
>
> Historical context lives in `docs/history/`. The retired status files
> `MESH_VK_REPORT.md`, `MIGRATION_REPORT.md`, `TEST_REPORT.md` were moved there
> on consolidation. The retired `CURRENT_ARCHITECTURE.md` is now a one-line
> redirect to this file. `README.md` is a short user-facing pointer.

---

## 1. Purpose

GPU Profiler is a single-user Windows dashboard that:

- Probes a fixed fleet of lab SSH hosts and the local PC for GPU/VRAM, RAM,
  CPU, top RAM processes, disk, and SSH reachability through multiple paths.
- Manages a single ChatGPT/Yandex selective PAC proxy (sing-box + PAC) on
  `127.0.0.1:10808` with PID-tracked start/stop and a PAC file served at
  `/chatgpt.pac`.
- Surfaces read-only status for overlays (NetBird `wt0`, ZeroTier, OpenVPN
  `outline-tap`), AmneziaVPN (full VPN), and public IP / mode (direct /
  selective proxy / full VPN).
- Runs an out-of-process **Mesh Route Watcher** (Windows Scheduled Task) that
  removes Amnezia-introduced Wi-Fi/LAN hijack routes after Amnezia Connect /
  on OpenVPN TAP up, and refreshes direct-site routes onto the current LAN
  gateway. The watcher never touches WFP / kill-switch / Block Internet.

GPU Profiler explicitly does **not** start, stop, or configure AmneziaVPN,
NetBird, or ZeroTier. Those are managed by their official apps and appear in
the UI as read-only pills.

---

## 2. Entry points

### 2.1 Desktop launchers (preferred)

Both `.exe` files live under `launchers/`. The canonical Desktop installer is
`launchers/install_desktop_launchers.ps1`, which rebuilds the EXEs with
`launchers/build.ps1` (csc / .NET Framework 4.x, no NuGet) and writes exactly
two Desktop shortcuts:

| Shortcut          | Target EXE              | Purpose                                                                                                  |
|-------------------|-------------------------|----------------------------------------------------------------------------------------------------------|
| `GPU Profiler.lnk`| `launchers\GPU Profiler.exe` | Starts (or opens) backend at `127.0.0.1:8765` via `scripts\start.ps1`, then opens the URL in the browser. |
| `Mesh Watcher.lnk`| `launchers\Mesh Watcher.exe` | Starts the `GPUProfiler-MeshRouteWatcher` Scheduled Task if installed; otherwise shows the install hint.  |

The legacy "GPU Profiler Stop" Desktop shortcut is **no longer created** —
backend stop is via `scripts\stop.ps1` only. (`scripts\create_desktop_shortcut.ps1`
is a compatibility wrapper that just calls the launcher installer.)

### 2.2 Backend boot path

1. `scripts\start.ps1`
   - If port `127.0.0.1:8765` is already accepting connections, opens the UI
     and exits 0.
   - Otherwise launches detached:
     `python -m uvicorn app:app --host 127.0.0.1 --port 8765`
     from the project root with `-WindowStyle Hidden`, redirecting stdout/stderr
     to `logs\uvicorn.out.log` and `logs\uvicorn.err.log`.
   - Waits up to 40s for `GET /` to return any HTTP status < 500.
   - Writes the listener PID to `logs\gpu_profiler.pid`.

2. `scripts\stop.ps1`
   - Kills the PID file and any other listener on `:8765` **only if**
     `Get-CimInstance Win32_Process` shows it is python / uvicorn / `app:app`.
   - Does **not** touch ChatGPT proxy, Amnezia, ZeroTier, NetBird.

3. `run.bat` is a one-liner convenience that calls `scripts\start.ps1`.

### 2.3 URLs

| URL                                       | What                                |
|-------------------------------------------|-------------------------------------|
| `http://127.0.0.1:8765/`                  | Single-page UI (`static/index.html`)|
| `GET /api/metrics`                        | Local + remote hosts (cached)       |
| `GET /api/network/public-ip`              | Public IP / mode (45s cache)        |
| `GET /api/network/mesh-health`            | NetBird + ZeroTier health (15s)     |
| `GET /api/mesh/watcher-status`            | Mesh Watcher heartbeat              |
| `POST /api/mesh/watcher-start`            | Start the scheduled task            |
| `GET /api/projects`                       | Project roots per host              |
| `GET /api/fs/{roots,list,repos}?host=…`   | Remote/local file browser           |
| `GET /api/vpn/status`                     | Read-only VPN/proxy composite       |
| `POST /api/vpn/ssh/{on,off}`              | ChatGPT selective proxy control     |
| `POST /api/vpn/{on,off,full/{on,off},netbird/{on,off}}` | **All return `ok: false` with a fixed error message** — Amnezia / Full TUN / NetBird are intentionally read-only. |
| `POST /api/open-cursor`                   | Open Cursor on a remote host        |
| `POST /api/open-agent` / `/api/agent/{session,chat,unlock}` | Optional Cursor SDK agent |
| `GET /chatgpt.pac`                        | PAC file (read from `~/.ssh/timeweb-vpn/chatgpt.pac`) |

Read-only monitoring endpoints are public to the bound interface. Mutating or
sensitive endpoints use `require_admin`: loopback clients are trusted; remote
clients must send `Authorization: Bearer <GPU_MONITOR_ADMIN_TOKEN>` or
`X-Admin-Token`. The canonical launcher still binds to loopback only.

---

## 3. Architecture

### 3.1 End-to-end flow

```
Desktop → launchers/GPU Profiler.exe
            └─ powershell scripts/start.ps1
                 └─ python -m uvicorn app:app --host 127.0.0.1 --port 8765
                      ├─ /static/index.html   (UI)
                      ├─ /api/metrics         → host_paths.py + remote_probe.py / local_probe.py
                      │     ├─ one scheduler task, independent per-host due times + backoff
                      │     ├─ asyncio.Semaphore(3) over host probes
                      │     ├─ ssh_runtime global hard cap 3 over metrics/routes/FS/agent/VPN SSH
                      │     ├─ preferred route first; alternate-route fan-out only after failure
                      │     └─ /api/metrics only reads cached / placeholder state
                      ├─ /api/network/public-ip → services/public_ip.py (ipapi.co, 45s cache)
                      ├─ /api/network/mesh-health → services/mesh_health.py (NetBird + ZT TCP, 15s cache)
                      ├─ /api/mesh/watcher-status → services/mesh_watcher_status.py (reads heartbeat JSON)
                      ├─ /api/vpn/ssh/{on,off} → services/proxy_manager.py → scripts/proxy/*.ps1
                      │     → sing-box.exe (outside repo, in ~/.ssh/timeweb-vpn/)
                      └─ /chatgpt.pac → proxy_manager.read_pac()
```

### 3.2 Out-of-process: Mesh Route Watcher

```
At logon Scheduled Task "GPUProfiler-MeshRouteWatcher"
└─ powershell scripts/amnezia/mesh_route_watcher.ps1 (Mutex: Global\GPUProfilerMeshRouteWatcher)
   ├─ A) Mesh fix (scripts/amnezia/fix_mesh_routes.ps1 as -AsLibrary)
   │     - Removes ONLY physical Wi-Fi/LAN hijack routes for:
   │         100.98.0.0/16 → wt0         (NetBird, probe 100.98.59.202)
   │         10.43.71.0/24 → ZT cds_team (probe 10.43.71.7)
   │         192.168.194.0/24 → ZT network_home (probe 192.168.194.7)
   │         172.24.158.182 → ZT nwid 0cccb752f7a913d8 (often ACCESS_DENIED)
   │         10.0.116.11/32 → outline-tap/OpenVPN/TAP (probe 10.0.116.11, good route may be 10.0.0.0/9)
   │     - ZeroTier self-nexthop rewrite: ZT /24 with NextHop=<own ZT IP> → on-link 0.0.0.0,
   │       plus a /32 host route for the probe IP, so Amnezia tun2 (0.0.0.0/1 + 128.0.0.0/1)
   │       does not steal sockets via LocalAddress=10.33.0.2.
   │     - Refuses to delete a hijack unless a good route on the right adapter is currently present.
   │     - NEVER touches WFP / Block Internet / default route / Amnezia tun2 /1 routes.
   ├─ B) Direct-site fix (scripts/amnezia/fix_direct_site_routes.ps1 as -AsLibrary)
   │     - Refreshes stale VK/Yandex/OpenVPN-external CIDR routes onto the current
   │       physical LAN/Wi-Fi gateway (lowest route+interface metric on 0.0.0.0/0,
   │       excluding tun/wt0/ZeroTier/vEthernet/etc).
   │     - Covered CIDRs: VK 87.240/93.186/95.213, Yandex 77.88/5.255/87.250/93.158/213.180,
   │       OpenVPN 185.178.210.151/32 and 185.178.210.152/32.
   ├─ Triggers: Amnezia tun2 Connect (rising edge), OpenVPN TAP up while Amnezia up,
   │            LAN gateway change, periodic safety check (45s) for tun2_steal/stale routes.
   └─ Heartbeat: runtime/mesh_route_watcher_status.json (UI reads it; UI never starts/stops watcher
      outside what `Mesh Watcher.exe` does via Task Scheduler).
```

The Mesh Watcher **does not depend on the GPU Profiler backend, port 8765, or
the UI**. It is a Scheduled Task running its own PowerShell process. UI status
is a passive read of its heartbeat JSON plus Task Scheduler state.

### 3.3 Out-of-process: ChatGPT PAC fallback

If port 8765 is down and a browser still wants PAC, `~/.ssh/timeweb-vpn/chatgpt-pac-server.ps1`
(optionally launched by `scripts\proxy\start_chatgpt_proxy.ps1`) serves PAC on
`127.0.0.1:18080`. Backend presence is not required for the proxy to function.

---

## 4. Project structure

> Conventions: paths are Windows-style with `\` for the in-tree PowerShell.
> Edit PowerShell only when behaviour changes — the GUI wires to scripts
> via absolute paths under the project root.

### 4.1 In-tree source

| Path                                     | Purpose                                                                                                | Caller                                | Safe to change? |
|------------------------------------------|--------------------------------------------------------------------------------------------------------|---------------------------------------|-----------------|
| `app.py`                                 | FastAPI app, all `/api/*` + `/chatgpt.pac`. Hosts the composite state, async caching, semaphore(3).    | `uvicorn` started by `scripts\start.ps1` | Yes, but every endpoint is wired in `static\index.html`. |
| `host_paths.py`                          | Per-host path probing: TCP + ssh BatchMode; best-path selection; `apply_protected_routes` helper.      | `app.py` (`/api/metrics`, `/api/protect-routes`) | Yes.            |
| `host_paths.json`                        | Per-host SSH/TCP/AnyDesk paths (ssh_target, ip, port, ssh_opts, prefer_for_probe, protected).          | `host_paths.py`                       | Yes.            |
| `protected_nets.json`                    | ZT nwids + protected prefixes + ssh hosts to never steal.                                              | `protect_routes.ps1`                  | Yes.            |
| `projects.json`                          | Remote project roots per host (used by `/api/projects` and Cursor shortcuts).                          | `cursor_projects.py`                  | Yes.            |
| `local_probe.py`                         | Local psutil + nvidia-smi + WDDM dedicated-GPU memory fill.                                            | `app.py` `/api/metrics` local card    | Yes.            |
| `remote_probe.py`                        | `nvidia-smi` CSV + processes/users/RAM/df/`du` — uploaded over SSH as a stdin payload.                 | `app.py` per SSH run                  | Yes.            |
| `local_browse.py` / `remote_browse.py` / `remote_fs.py` | File-browser endpoints `/api/fs/{roots,list,repos}`.                                     | `app.py`                              | Yes.            |
| `cursor_projects.py`                     | Project discovery + open-in-Cursor (local + remote).                                                   | `app.py` `/api/open-cursor`, `/api/projects` | Yes.       |
| `sdk_agent.py`                           | Optional Cursor SDK agent session driver (CURSOR_API_KEY from `.env`).                                 | `app.py` `/api/open-agent`, `/api/agent/*` | Yes; requires `CURSOR_API_KEY`. |
| `window_capture.py`                      | Window helper for the agent workflow.                                                                  | not currently invoked by `app.py`     | Yes, isolated.  |
| `static/index.html`                      | Single-file SPA (vanilla JS, no build step). References all endpoints by name.                        | uvicorn                               | Yes, but keep all `/api/*` calls in sync. |
| `requirements.txt`                       | `fastapi`, `uvicorn[standard]`, `psutil`, `pydantic`.                                                  | `start.ps1`                           | Yes.            |
| `.env` / `.env.example`                  | Secrets (`CURSOR_API_KEY`). `.env` is gitignored; `.env.example` is the template.                      | `app.py._load_dotenv`                 | Yes.            |
| `run.bat`                                | Convenience wrapper around `scripts\start.ps1`.                                                        | manual / shortcuts                    | Yes.            |
| `services/__init__.py`                   | Marker.                                                                                                | —                                     | —               |
| `services/mesh_health.py`                | NetBird + ZeroTier TCP health (diagnose-only, no mutation). Cached 15s.                                | `app.py` `/api/network/mesh-health`   | Yes.            |
| `services/mesh_watcher_status.py`        | Reads Mesh Watcher heartbeat JSON + Task Scheduler state; can enable+Start the task.                    | `app.py` `/api/mesh/*`                | Yes.            |
| `services/public_ip.py`                  | Public IP via ipapi.co; 45s cache; base-IP persistence in `logs\public_ip_state.json`.                 | `app.py` `/api/network/public-ip`     | Yes.            |
| `services/proxy_manager.py`              | Wraps `scripts\proxy\*.ps1` for ChatGPT proxy start/stop/status/PAC.                                   | `app.py` `/api/vpn/ssh/*`, `/chatgpt.pac` | Yes.        |

### 4.2 Scripts

| Path                                     | Purpose                                                                                                | Caller                                | Safe to change? |
|------------------------------------------|--------------------------------------------------------------------------------------------------------|---------------------------------------|-----------------|
| `scripts/start.ps1`                      | Detached backend boot. Writes `logs\gpu_profiler.pid`.                                                 | Desktop / `run.bat` / `launchers\GPU Profiler.exe` | Yes. |
| `scripts/stop.ps1`                       | PID-safe stop of backend (python/uvicorn on `:8765`).                                                  | manual                                | Yes.            |
| `scripts/create_desktop_shortcut.ps1`    | Compatibility wrapper → `launchers\install_desktop_launchers.ps1`.                                     | manual (legacy)                       | Yes, keep as wrapper. |
| `scripts/proxy/start_chatgpt_proxy.ps1`  | PID-safe start of sing-box.exe (`~/.ssh/timeweb-vpn\config-chatgpt.json`) on `:10808`. Optional PAC fallback server on `:18080`. | `services/proxy_manager.py` → `scripts\start.ps1` (via task) | Yes. |
| `scripts/proxy/stop_chatgpt_proxy.ps1`   | Stops only sing-box whose cmdline contains `config-chatgpt.json`. Never mass-kills.                    | `services/proxy_manager.py`           | Yes.            |
| `scripts/amnezia/mesh_route_watcher.ps1` | The watcher loop (mesh + direct-site, heartbeat, mutex, scheduled task entry).                        | `GPUProfiler-MeshRouteWatcher` Task   | Yes, but its contract (heartbeat JSON shape, status values) is read by `services/mesh_watcher_status.py` and rendered in the UI. |
| `scripts/amnezia/fix_mesh_routes.ps1`    | Library + standalone script. Removes physical hijack routes, rewrites ZT on-link routes, ensures host /32. `-WhatIf` is supported; `-AsLibrary` dot-sources. | dot-sourced by watcher; runnable manually with `-WhatIf` | Yes.  |
| `scripts/amnezia/fix_direct_site_routes.ps1` | Library + standalone. Refreshes VK/Yandex/OpenVPN-external CIDRs onto current LAN gateway. `-WhatIf` and `-AsLibrary` supported. | dot-sourced by watcher; runnable manually with `-WhatIf` | Yes. |
| `scripts/amnezia/fix_vk_wfp_excludes.ps1`| **Deprecated wrapper** that just calls `fix_direct_site_routes.ps1`. Earlier versions edited WFP — that was retired. | manual (legacy) | Treat as deprecated; do not reintroduce WFP edits here. |
| `scripts/amnezia/install_mesh_route_watcher_task.ps1` | Registers `GPUProfiler-MeshRouteWatcher` (AtLogOn, Highest, restart-on-failure). Admin required. | manual (one-time)            | Yes.            |
| `scripts/amnezia/uninstall_mesh_route_watcher_task.ps1` | Removes task + stops running watcher process. Admin required.                              | manual                                | Yes.            |
| `scripts/amnezia/configure_split_tunnel.ps1` | **READ-ONLY helper.** Prints the suggested `ExceptSites` from `config\amnezia_except_sites.txt`. It does **not** write the registry. | manual                  | Yes, but keep it read-only — see safety rules. |
| `protect_routes.ps1`                     | Legacy helper to re-add ZT on-link routes after VPN toggles. Called from `POST /api/protect-routes`. Backend startup **no longer** runs it automatically — see "Why startup does not auto-rewrite routes". | `app.py` `/api/protect-routes` (manual) | Yes, but treat as legacy. |

### 4.3 Launchers

| Path                                                | Purpose                                                                                          |
|-----------------------------------------------------|--------------------------------------------------------------------------------------------------|
| `launchers/GPU Profiler.exe` / `launchers/Mesh Watcher.exe` | Compiled with `csc / .NET Framework 4.x` (no NuGet). Rebuild with `launchers\build.ps1`.  |
| `launchers/src/GpuProfilerLauncher.cs`              | Starts `scripts\start.ps1` if `:8765` is closed, then opens browser. Mutex `Local\GPUProfiler.Launcher.8765`. |
| `launchers/src/MeshWatcherLauncher.cs`              | Uses Schedule.Service COM (no NuGet). If task exists and is not Running, calls `Run`. If missing, shows install hint. |
| `launchers/build.ps1`                               | Compiles both EXEs from `src\*.cs` with `csc.exe`.                                               |
| `launchers/install_desktop_launchers.ps1`           | Builds EXEs and writes exactly two Desktop shortcuts: `GPU Profiler.lnk`, `Mesh Watcher.lnk`. Removes legacy clutter (`GPU Profiler Stop.lnk`, `GPU Profiler.bat`, `Mesh Watcher.bat`, etc). |

### 4.4 Runtime / Logs / Config

| Path                                  | Purpose                                                                                       |
|---------------------------------------|-----------------------------------------------------------------------------------------------|
| `logs/gpu_profiler.pid`               | Listener PID written by `start.ps1`; consumed by `stop.ps1`.                                  |
| `logs/uvicorn.out.log` / `uvicorn.err.log` | Backend stdout/stderr.                                                                      |
| `logs/public_ip_state.json`           | Last-known base IP (egress IP when Amnezia was OFF).                                           |
| `logs/mesh_route_watcher.log`         | Watcher log, rotated at 2 MiB → `mesh_route_watcher.log.1`.                                    |
| `runtime/mesh_route_watcher.pid`      | Watcher PowerShell PID.                                                                       |
| `runtime/mesh_route_watcher_status.json` | Heartbeat JSON consumed by UI.                                                              |
| `runtime/.gitkeep`                    | Placeholder.                                                                                  |
| `config/amnezia_except_sites.txt`     | Reference list for the Amnezia `ExceptSites` (read by `configure_split_tunnel.ps1` and intended for Amnezia UI). |
| `config/examples/*.example.json`      | Templates for `host_paths.json`, `projects.json`, `protected_nets.json`.                       |
| `backup/legacy/timeweb-vpn/`          | Archived pre-reorg proxy / full-TUN scripts. Never executed by current code.                  |
| `backup/amnezia_mesh_YYYYMMDD_HHMMSS/`| Ad-hoc route/WFP snapshots taken during incidents (manual).                                   |
| `_agent_workspaces/<host>/REMOTE.md`  | Per-host remote project context consumed by the Cursor SDK agent.                             |

### 4.5 Outside the repo (must stay outside)

- `~/.ssh/config` — OpenSSH host aliases (`aicenter1`, `aicenter2`, `aicenter3`,
  `aicenteritl`, `ml3`, `ml4`, `h200`, `lab_comp`, `cds2`, `timeweb-vps`, …).
- `~/.ssh/timeweb-vpn/` — `sing-box.exe`, `config-chatgpt.json` (Shadowsocks
  secrets), `chatgpt-proxy-lib.ps1`, `chatgpt.pac`, `chatgpt-pac-server.ps1`,
  PID files. Treated as a local runtime dependency: binaries + secrets stay
  off GitHub; GPU Profiler owns PID-safe wrappers in `scripts\proxy\`.
- Installed Amnezia / ZeroTier / NetBird applications and system services.
- `C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat` — read by both Python
  and PowerShell to enumerate networks.
- `C:\Program Files\Netbird\netbird.exe` — referenced for read-only status
  collection in `app.py._collect_vpn_status`.

---

## 5. Monitoring

### 5.1 What `/api/metrics` collects per host

For each host in `HOSTS = [lab_comp, ml3, ml4, aicenteritl, aicenter1, aicenter2, aicenter3, h200]`:

| Field        | Source                                                                            |
|--------------|-----------------------------------------------------------------------------------|
| GPU cards    | `nvidia-smi --query-gpu=index,name,uuid,memory.used,memory.total,utilization.gpu`  |
| GPU procs    | `nvidia-smi --query-compute-apps=…` + Windows `typeperf \\GPU Process Memory(*)\\Dedicated Usage` to fill WDDM N/A values |
| Users        | Derived from `/proc/<pid>/status` Uid → `pwd.getpwuid` (remote) or `psutil` (local) |
| RAM          | `free -b` (remote) / `psutil.virtual_memory()` (local)                            |
| Top RAM procs| `psutil.process_iter` (top 15 by RSS) on local; for remote only GPU procs are returned |
| Disk         | `df -B1 -P $HOME /data /data2` (remote)                                            |
| Home         | `du -sb $HOME` capped 12s (then unbounded fallback)                               |
| Latency      | Wall-clock around the SSH call                                                     |
| Path probes  | All paths from `host_paths.json[host]` (see §6.3)                                  |

For `local` the card is served from `local_probe.probe_local()` and refreshed
at `LOCAL_REFRESH_SEC = 2.0` independently of the remote sweep.

### 5.2 Concurrency, timeouts, caching

- A single `_metrics_scheduler()` owns regular collection. Default successful
  interval is 15s (`GPU_MONITOR_REFRESH_SEC`); failures back off exponentially
  up to 120s (`GPU_MONITOR_BACKOFF_MAX_SEC`). `/api/metrics` never starts SSH.
- `_host_tasks` prevents duplicate probes per host. `_host_generation` prevents
  a late DELETE→ADD result from being committed to the new host identity.
- `asyncio.Semaphore(3)` caps concurrent host probes. `ssh_runtime.py` adds the
  authoritative process-wide cap (default 3, `GPU_MONITOR_SSH_MAX_ACTIVE`) to
  metrics, alternate-route checks, file browsing, SDK-agent tools and legacy
  VPN SSH checks. Diagnostics: authenticated `GET /api/diagnostics/ssh`.
- A successful regular probe does not re-check every route. Cached route
  diagnostics live for 300s and full diagnosis is triggered after failure.
- Failed probes preserve the last successful GPU/RAM/disk payload with
  `stale=true`, `connection_ok=false`, `last_success_at`, `last_attempt_at`
  and the current connection error.
- Every successful host snapshot is atomically persisted under
  `data/metrics/last_good/<host>.json`. Startup restores it as stale before
  scheduling SSH, so a backend restart never replaces known metrics with a
  loading placeholder. These runtime files are gitignored and excluded from
  source archives.
- `SSH_TIMEOUT_SEC = 8`, `SSH_JUMP_TIMEOUT_SEC = 14` (`app.py`). `host_paths.py`
  uses 5–12s per path depending on ProxyJump and target.
- `_path_cache` in `host_paths.py` keeps route diagnostic results 300s.
- `services/mesh_health.py` caches 15s. `services/public_ip.py` caches 45s and
  persists the base IP.
- `_vpn_cache` in `app.py` caches 10s but always re-checks proxy TCP ports
  cheaply so the UI does not stick on stale OFF/ON.
- `services/proxy_manager.status()` always re-TCP-checks `:10808` so UI Start
  appears immediately.

### 5.3 Dead-host handling

- `host_paths.py.probe_path` returns the failure detail (last stderr line,
  `"timeout"`, or `"tcp <host>:<port> closed"`). No host is dropped from the
  list on failure.
- `services/mesh_health._probe_group` classifies each overlay as
  `OK` / `BLOCKED` (WSAEACCES) / `DOWN` and returns a `hint` describing the
  cause (WFP kill-switch, peer offline, etc).
- `/api/metrics` returns placeholders with `reachable=false` and `error=загрузка…`
  on cold cache, and never blocks the UI behind a long SSH sweep.

### 5.4 Backend process model

The in-memory cache, scheduler and SSH limiter are process-local. Production
must use exactly one uvicorn worker; `scripts/start.ps1` intentionally launches
uvicorn without `--workers` or `--reload`. Multiple workers would create
independent collectors and are unsupported unless state/scheduling is first
moved to an external coordinator.

### 5.5 Frontend update invariants

- `static/app.js?v=23` permits only one in-flight metrics request and rejects
  responses that predate an ADD/DELETE mutation.
- Known metrics are retained when an older backend returns a loading/error
  placeholder; the card becomes stale rather than empty.
- Cards are keyed by host and are not unconditionally re-appended on every
  tick. While a settings (gear) menu is open, render updates do not replace or
  move cards; window/grid scroll positions are restored after normal updates.
- Successful UI ADD/DELETE mutations are overlaid on metrics responses and
  persisted as hostname sets in browser localStorage. An old aggregate
  snapshot therefore cannot temporarily remove a newly added card or resurrect
  a deleted one; backend rows may update the card but not reverse the mutation.
- Display state has the priority `success > concrete connection error > loading`.
  The latest concrete per-host error is kept in browser localStorage, so a
  later polling placeholder cannot replace `connection timed out` with
  `loading`; polling itself continues normally in the background.

### 5.6 Users and host visibility

- `data/users.sqlite3` is the canonical store for users, IP bindings, host
  ownership, visibility and SSH path JSON; it must not be committed.
- The request client IP remembers the selected user. Any non-empty valid user
  name is created without a password. The reserved `admin` user requires
  `GPU_MONITOR_ADMIN_PASSWORD` (default `0000` for the requested local setup).
- Core hosts are `aicenter<number>`, `aicenteritl` and `h200`. Core and shared
  hosts are visible to everyone. Admin additions are shared; normal-user
  additions are private to their owner.
- Admin may delete any host. A normal user may delete only their own private
  hosts. The backend enforces this rule; hiding the gear icon is only a UI aid.
- Admin may set a per-host `display_name`. Renaming is presentation-only and
  must never change the SSH hostname, scheduler key, history key or cache path.
- `host_paths.json` is a runtime compatibility mirror of the SQLite inventory,
  not the ownership authority.

---

## 6. SSH topology

The fixed inventory of SSH hosts is in `app.py.HOSTS` and `host_paths.json`.
Names below are `~/.ssh/config` aliases and the canonical IPs that should be
used when probing.

### 6.1 Overlays / virtual networks

| Overlay      | CIDR / range       | Windows adapter              | Host where it lives       |
|--------------|--------------------|------------------------------|---------------------------|
| **NetBird**  | `100.98.0.0/16`    | `wt0`                        | `cds2` NetBird peer `100.98.2.11` (used as `ProxyJump cds2`) |
| **ZeroTier cds_team** | `10.43.71.0/24` | `ZeroTier One [93c72639168b9551]` | lab_comp ZT IP `10.43.71.7` |
| **ZeroTier network_home** | `192.168.194.0/24` | `ZeroTier One [60ee7c034a970d9d]` | network_home remote `192.168.194.7`, Windows `192.168.194.39` |
| **ZeroTier 172** | `172.24.0.0/16` (specifically `172.24.158.182`) | `ZeroTier One [0cccb752f7a913d8]` | currently often `ACCESS_DENIED` until the Windows node is authorized on this network |
| **OpenVPN Personal-2** | `10.0.116.11/32` host route; `10.0.0.0/9` overlay | `outline-tap` / OpenVPN / TAP-Windows | h200 |

> **CIDR rule for 172.24 / ZT 172**: do not assume a subnet prefix. The only
> IP confirmed in `host_paths.json` and the watcher is `172.24.158.182`. If
> ZeroTier network `0cccb752f7a913d8` reports `ACCESS_DENIED` in
> `runtime\mesh_route_watcher_status.json` / `services\mesh_health.py`, that
> is the current state — do not invent a CIDR.

### 6.2 Per-host path inventory

`prefer_for_probe: true` is what the metrics probe uses by default; the other
paths exist for fallback and reachability pills in the UI.

#### `lab_comp` (10.43.71.7)

- `ssh_config` (preferred) — `ssh lab_comp` → uses `~/.ssh/config` and lands
  on `10.43.71.7` via `ProxyJump cds2`. The `cds2` alias in `~/.ssh/config`
  targets the **NetBird** address `100.98.2.11`, not ZeroTier.
- `zt_jump_cds2` — explicit `ProxyJump=cds2` with `IdentitiesOnly=yes`.
- `zt_cds_direct` — ZeroTier direct (`ProxyJump=none`). Direct
  Windows→`10.43.71.7` is often black-holed by ISP routing.
- `zt_home` — ZeroTier network_home direct to `192.168.194.7` (`require_ssh`).
- `zt_172` — ZT 172.24.158.182 (see note above about ACCESS_DENIED).
- `lan` — LAN `192.168.50.18` direct.
- `anydesk` — id `582031545`, roster-only (not a live-session check).

> **lab_comp_direct_ok.exe.** No such binary exists in this repository. The
> equivalent "direct OK" path for `lab_comp` is the `ssh_config` row of
> `host_paths.json` (uses `ssh lab_comp` → `ProxyJump cds2` via NetBird). The
> Mesh Route Watcher keeps the path healthy by removing Amnezia Wi-Fi
> hijacks and rewriting the ZeroTier self-nexthop routes to on-link.

#### `aicenter1` / `aicenter2` / `aicenter3` (NetBird peers)

`100.98.208.203`, `100.98.59.202`, `100.98.241.137`. Each has a preferred
`ssh_config` row plus a `netbird` direct (`100.98.x.y`), a `zt_cds` zero-tier
fallback (`10.43.71.82/124`), and `lan_campus` (`10.55.229.159`, `10.55.230.12`,
`10.55.228.129`). `aicenteritl` has an extra `public_old` legacy at
`93.175.29.227`.

#### `aicenteritl` (100.98.50.236)

Same pattern as above plus ZT cds at `10.43.71.124`, LAN at `10.55.230.36`,
and the historical public IP `93.175.29.227`.

#### `h200` (10.0.116.11)

`ssh h200` lands on `10.0.116.11:30101` over the OpenVPN Personal-2 tunnel
(`outline-tap` / OpenVPN / TAP-Windows). The NetBird/OpenVPN good-route prefix
in the watcher is `10.0.116.` and `10.0.0.0/9` — the OpenVPN overlay often
covers h200 via a broader route, not just the host route.

#### `ml3` / `ml4` (gater.frccsc.ru)

Public IP `83.149.227.22`, ports `9189` and `9191` via the `gater` front door
(`gater.frccsc.ru`). `~/.ssh/config` aliases resolve here.

### 6.3 TCP :22 open ≠ healthy SSH

`host_paths.py` only treats TCP `:22` as an **early bailout**: when TCP fails,
the path is marked `tcp <host>:<port> closed` and SSH is not attempted.
TCP open alone is **not** considered "SSH OK" — `host_paths.py` still runs
`ssh -o BatchMode=yes <target> echo OK` and only marks `ok=true` if the literal
`OK` is observed on stdout with exit code 0. The `tcp_only` flag exists for
non-SSH paths; combined with `prefer_for_probe` it would bypass SSH but no
path in `host_paths.json` uses that combination.

### 6.4 `Connection to UNKNOWN port 65535 timed out`

When a path uses `ProxyJump`, OpenSSH reports progress on the jump first.
If the **jump** itself times out, the trailing message can be
`Connection to UNKNOWN port 65535 timed out` — `65535` is a placeholder,
not the real port on the destination host. Treat it as a ProxyJump failure,
not a problem on the destination. (`host_paths.py` returns the last stderr
line verbatim.)

---

## 7. `lab_comp` reachability chain (alias logic)

The `lab_comp` SSH alias resolves to:

```
ssh lab_comp
  → ~/.ssh/config Host lab_comp: ssh lab_comp → ssh lab_comp = ProxyJump cds2
  → cds2 = 100.98.2.11 (NetBird)
  → from cds2, ssh to lab_comp at 10.43.71.7 (ZeroTier cds_team)
```

So the path is **direct NetBird → ZeroTier** through a NetBird peer. There is
no separate `lab_comp_direct_ok.exe` binary — the host_paths entry called
`ssh_config` is what gives us a direct, working SSH path; if the watcher is
healthy (wt0 + ZT cds_team routes on the right adapters, no Amnezia hijack,
no tun2_steal), `ssh lab_comp` succeeds.

Verification recipe (matches `services/mesh_health._zerotier_health`):

1. `Find-NetRoute -RemoteIPAddress 100.98.59.202` → must be `wt0`.
2. `Find-NetRoute -RemoteIPAddress 10.43.71.7` → must be `ZeroTier One [93c72639168b9551]`,
   and the route's NextHop must be `0.0.0.0` (on-link) — see §8.
3. `ssh -o BatchMode=yes -o ConnectTimeout=5 lab_comp echo OK` → must return `OK`.

---

## 8. ZeroTier self-nexthop incident (resolved invariant)

### 8.1 The bug

Windows ZeroTier installs routes like:

```
192.168.194.0/24   NextHop = 192.168.194.39   (the Windows node's own ZT IP)
10.43.71.0/24      NextHop = 10.43.71.40      (the Windows node's own ZT IP)
```

When Amnezia is also up (its tun2 injects `0.0.0.0/1` and `128.0.0.0/1`),
Windows route selection sometimes picks tun2 because the more-specific /24 is
hidden behind the tun2 default routing in some revs of `Find-NetRoute`. The
practical symptom is that `ssh 10.43.71.7` opens a socket with
`LocalAddress = 10.33.0.2` (the Amnezia tun2 address) and the packet never
reaches the ZT adapter — connection times out or returns
`Connection to UNKNOWN port 65535 timed out` (see §6.4).

### 8.2 The fix

`scripts/amnezia/fix_mesh_routes.ps1 :: Repair-ZeroTierOnLinkRoutes`:

- For each ZeroTier adapter that owns one of the protected prefixes, rewrite
  routes whose `NextHop == <own ZT IP>` to `NextHop = 0.0.0.0` (on-link), so
  `Find-NetRoute` no longer needs to forward to a self address.
- Drop the ZT adapter's `InterfaceMetric` to `1` so it outranks tun2's `IM=5`
  for the /24.
- Ensure a `/32` on-link route for each mesh probe IP
  (`10.43.71.7/32`, `192.168.194.7/32`, `172.24.158.182/32`). The /32 beats
  tun2 even if the /24 flap-resets back to a self-nexthop. These host routes
  also keep `LocalAddress` on the ZeroTier adapter rather than `10.33.0.2`.

### 8.3 Verified behaviour

After the watcher runs (`Mesh Watcher.exe` or initial `/api/mesh/watcher-start`):

- `ssh -o BatchMode=yes 192.168.194.7 echo OK` → `OK`, `LocalAddress = 192.168.194.39`.
- `ssh -o BatchMode=yes 10.43.71.7 echo OK` → `OK`, `LocalAddress = 10.43.71.40`.
- `ssh lab_comp` (via ProxyJump cds2) works.

`runtime/mesh_route_watcher_status.json` carries the post-fix state with
`zerotier_route`, `zerotier_home_route`, `zerotier_172_route` in
`OK` / `HIJACKED` / `TUN2_STEAL` / `MISSING` / `UNKNOWN`. `OK` means the
probe IP finds its expected ZT adapter in `Find-NetRoute`. **Do not** treat
`Get-NetRoute` alone as success — the verification step in §12 always
re-checks the actual source address via the SSH connection itself.

---

## 9. Amnezia

### 9.1 Modes / interfaces

- `tun2` (Xray / tun2socks) — the modern Amnezia Full-VPN path on this
  machine. `app.py._vpn_service_state` checks `tun2 Status == Up` first.
- `AmneziaWGTunnel*` — older AmneziaWG service. Still queried as a fallback.
- `AmneziaWGTunnel$pcawg` — known legacy service name kept as fallback.
- `app.py._vpn_service_state` does **not** treat `AmneziaVPN.exe` alone as
  connected (the GUI can sit idle).

### 9.2 Split tunneling (current policy)

- Mode: **`VpnAllExceptSites`** — full VPN for everything except listed sites.
- Sites (CIDR / domains, in `config\amnezia_except_sites.txt`):
  - `100.98.0.0/16`, `10.43.71.0/24`, `192.168.194.0/24`, `172.24.0.0/16` —
    mesh overlays.
  - VK domains (`vk.com`, `www.vk.com`, `m.vk.com`, `api.vk.com`,
    `login.vk.com`, `id.vk.com`, `userapi.com`, `vk-cdn.net`,
    `vkuservideo.net`, `queuev4.vk.com`) and the VK edge CIDRs
    `87.240.0.0/16`, `93.186.224.0/20`, `95.213.0.0/16`.
  - Yandex domains (`yandex.ru`, `www.yandex.ru`, `ya.ru`, `yandex.net`,
    `yastatic.net`) and `77.88.0.0/16`, `5.255.0.0/16`, `87.250.0.0/16`,
    `93.158.0.0/16`, `213.180.0.0/16`.
  - `10.0.116.11` (h200 host) — only relevant if Amnezia is up and the user
    wants h200 to bypass the tunnel; the Mesh Watcher still keeps
    `10.0.116.11/32` on `outline-tap`.

### 9.3 Amnezia, bypass, and physical routes

The Mesh Watcher only acts on **physical** Wi-Fi/LAN interfaces that Amnezia
added. It does **not** start, stop, or reconfigure Amnezia. Amnezia may both:

1. **Allow bypass** for an ExceptSite via `WFP Allow Exclude route` (so the
   kill-switch lets non-VPN traffic for that CIDR through), and
2. **Create a physical Wi-Fi/LAN route** with the LAN gateway as NextHop so
   the bypassed traffic actually exits on the home internet.

The watcher is required for case (2): the WFP exclude is not enough — without
removing the physical hijack, the bypassed packet can still end up on the
wrong interface.

### 9.4 Configuring Amnezia (the only correct way)

- `scripts/amnezia/configure_split_tunnel.ps1` is **READ-ONLY**: it just
  prints the contents of `config/amnezia_except_sites.txt`. Do not extend it
  to write the registry. See safety rules (§11).
- Real configuration of `ExceptSites` happens **only** in the official
  AmneziaVPN UI (`VpnAllExceptSites` + per-site entries), followed by
  **Disconnect → Connect** so the kill-switch WFP is rebuilt with the new
  excludes. Source of truth for what Amnezia sees as configured is the
  Amnezia UI, not the Windows registry.

### 9.5 `apply_protected_routes` on startup

`protect_routes.ps1` exists and is reachable via `POST /api/protect-routes`,
but **`app.py` no longer calls it on startup** (`_startup_probe_local` only
schedules `_refresh_local`). The reason is recorded in the source comment:

> Overlay breakage is usually Amnezia kill-switch WFP (WSAEACCES), not
> missing routes — see mesh health API. Re-adding routes does not unblock
> WFP; it just wastes time before the UI is up.

If you change this, also re-check `services/mesh_health.py` (which already
classifies WSAEACCES as `BLOCKED` with a hint pointing at Amnezia ExceptSites).

---

## 10. Mesh Route Watcher

### 10.1 Installation

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\amnezia\install_mesh_route_watcher_task.ps1
```

Admin required. Registers Scheduled Task `GPUProfiler-MeshRouteWatcher`
(`AtLogOn`, `Highest`, restart on failure, ignore new instances, no execution
time limit). Starts the task on completion.

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\amnezia\uninstall_mesh_route_watcher_task.ps1
```

Stops the task, deletes it, and force-kills any leftover watcher process
matched by CommandLine containing `mesh_route_watcher.ps1`.

### 10.2 Behaviour summary

- Single instance enforced by mutex `Global\GPUProfilerMeshRouteWatcher`.
- Heartbeat JSON at `runtime\mesh_route_watcher_status.json` (UTF-8, no BOM).
  UI reads it via `services/mesh_watcher_status.get_watcher_status`.
- Status JSON fields:
  `running, pid, ts, last_check, last_fix, last_event, amnezia_up, openvpn_up,
   netbird_route, zerotier_route, zerotier_home_route, zerotier_172_route,
   h200_route, lan_gateway, lan_iface, vk_route, yandex_route,
   last_gateway_change, last_error, last_removed`.
- Route-state strings (uppercased for the UI): `OK`, `HIJACKED`, `TUN2_STEAL`,
  `MISSING`, `UNKNOWN`.

### 10.3 Triggers

| Trigger                                                 | Watcher action |
|---------------------------------------------------------|----------------|
| `tun2` rises while `tun2` was down                      | Wait 3s, then mesh fix + direct-site refresh |
| OpenVPN TAP rises while Amnezia is up                   | Wait 3s, then mesh fix only (h200 stays on TAP) |
| LAN gateway `NextHop` changes                           | Direct-site refresh only (mesh routes don't depend on LAN gw) |
| Periodic safety check (45s) while Amnezia is up         | If tun2_steal or stale direct-site route detected, fix |
| Amnezia `tun2` falls (Disconnect)                       | Log only, **no route changes** |
| Amnezia down, no event                                  | Idle heartbeat only |

### 10.4 Good-path requirement

`fix_mesh_routes.ps1 :: Invoke-MeshRouteFix` refuses to delete a hijack
unless `Test-GoodMeshPathPresent -Target <t>` returns `true`: there must be
at least one route on the **good** adapter whose `DestinationPrefix` matches
the target's `GoodRoutePrefixes` (e.g. `wt0` with a `100.98.` prefix for
NetBird). If that condition fails, the route is logged as a warning and
**skipped** — never blindly deleted.

For ZT 172 and OpenVPN-h200 specifically, the watcher logs
`WARN skip (no good overlay/OpenVPN path present)` whenever the underlying
adapter is not up. This is the current state in `logs\mesh_route_watcher.log`
when ZT 172 is `ACCESS_DENIED` and OpenVPN is down.

---

## 11. WFP / kill-switch safety rules

These rules exist because of an earlier incident in which a "fix" attempted
to disable parts of Amnezia's WFP block filters and a hand-written REG_BINARY
key — see `docs/history/MESH_VK_REPORT.md` for the post-mortem. They are
**load-bearing**.

**Forbidden actions:**

1. Never delete or modify Amnezia's `Block Internet` / kill-switch WFP filters
   (`FwpmFilterDeleteById0`, `netsh wfp …`, etc.) to "fix" connectivity.
   The block is what keeps the rest of the system safe while the full VPN is
   up. Disabling it does not fix anything that real configuration would fix.
2. Never mass-call `FwpmFilterDeleteById0` as a workaround for "I can't reach
   a CIDR through Amnezia". If the IP cannot exit via the VPN and is not in
   `ExceptSites`, it is behaving as designed — fix the policy, not the WFP.
3. Never toggle the kill-switch globally.
4. Never create fake WFP permits in any sublayer. WFP rule authorship is
   outside this project's scope.
5. Never write to the internal Amnezia registry binary by hand. Earlier
   versions of this repo wrongly created
   `HKCU\Software\AmneziaVPN.ORG\AmneziaVPN\Conf\ExceptSites\`
   as a sub-**key** with empty string values. **Amnezia ignores that key.**
   The real configuration is a Qt `@Variant` REG_BINARY value on the parent
   `Conf` value name `ExceptSites`, written only by the official Amnezia app.
   The backup of the bogus subkey is kept at
   `logs\fake_ExceptSites_subkey_*.reg` as evidence; do not revert it to a
   write step.

**Required workflow when a user reports "Amnezia blocks X":**

1. Diagnose. Confirm `WSAEACCES` / `10013` on the destination. Get the
   destination IP/CIDR.
2. Determine which interface the user *intended* (NetBird, ZT, OpenVPN,
   direct LAN).
3. Verify a good path actually exists on that interface
   (`Find-NetRoute`, `Get-NetRoute`, ping, SSH direct).
4. Backup the current state (`Get-NetRoute | Export-CliXml`,
   `netsh wfp show filters > snapshot.xml`, `reg export` if touching
   registry).
5. Make the minimal change (Amnezia `ExceptSites` UI entry, then Disconnect
   → Connect). Never edit WFP directly.
6. Verify the actual socket source address via `ssh -vvv` /
   `Test-NetConnection`. Re-check unrelated networks and confirm
   `Block Internet` is still present in WFP.
7. Never declare success on `Get-NetRoute` alone.

---

## 12. Direct-site routes (VK / Yandex / OpenVPN external)

`scripts\amnezia\fix_direct_site_routes.ps1` is a sibling concern to the mesh
fix — it is dot-sourced by the same watcher.

- **Scope:** physical Wi-Fi/LAN default gateway only. Excluded from
  "good": any interface matching `tun|wt0|ZeroTier|vEthernet|Loopback|
  Tailscale|Wintun|Amnezia|outline-tap|OpenVPN|TAP-Windows`.
- **Covered CIDRs:**
  - VK: `87.240.0.0/16`, `93.186.224.0/20`, `95.213.0.0/16`
  - Yandex: `77.88.0.0/16`, `5.255.0.0/16`, `87.250.0.0/16`,
    `93.158.0.0/16`, `213.180.0.0/16`
  - OpenVPN external endpoints: `185.178.210.151/32`, `185.178.210.152/32`
    (so a Yandex / VPN endpoint that the OpenVPN provider resolves to does
    not get nested via Amnezia `tun2`)
- **Trigger:** watcher refreshes these on every Amnezia Connect rising edge
  and on LAN gateway change. Periodic safety check (45s) also fires if a
  route is stale (NextHop / ifIndex no longer matching the current gateway).
- **Not the same as mesh routing.** Mesh routes go to overlays
  (`wt0`, ZeroTier, OpenVPN TAP); direct-site routes go to the LAN gateway.
  Do not merge the two scopes.

---

## 13. OpenVPN / h200

- The internal h200 IP is `10.0.116.11`. It is reached via the OpenVPN
  Personal-2 tunnel on `outline-tap` / OpenVPN / TAP-Windows adapters, port
  `30101`.
- External OpenVPN endpoints are `185.178.210.151` and `185.178.210.152`
  (the OpenVPN provider's edge servers). These are added to the direct-site
  CIDR list so they do not nest via Amnezia tun2.
- The watcher treats both as separate targets: h200 must stay on OpenVPN TAP
  (mesh scope), the external endpoints must stay on the LAN gateway
  (direct-site scope).
- Do **not** confuse the external VPN server (`185.178.210.x`) with the
  internal `h200` (`10.0.116.11`).

---

## 14. Verification

After any change that touches ZeroTier / NetBird / OpenVPN / Amnezia, run:

```powershell
# 1) Find the actual route(s)
Find-NetRoute -RemoteIPAddress 100.98.59.202      # expect via wt0
Find-NetRoute -RemoteIPAddress 10.43.71.7         # expect via ZeroTier One [93c72639168b9551]
Find-NetRoute -RemoteIPAddress 192.168.194.7      # expect via ZeroTier One [60ee7c034a970d9d]
Find-NetRoute -RemoteIPAddress 172.24.158.182     # may be missing / ACCESS_DENIED — that is the current state
Find-NetRoute -RemoteIPAddress 10.0.116.11        # expect via outline-tap / OpenVPN / TAP

# 2) Layer-3 reachability (where allowed)
Test-NetConnection 100.98.59.202 -Port 22
Test-NetConnection 10.43.71.7    -Port 22

# 3) End-to-end SSH (this is what actually matters)
ssh -vvv -o BatchMode=yes -o ConnectTimeout=5 aicenter2 echo OK
ssh -vvv -o BatchMode=yes -o ConnectTimeout=5 lab_comp echo OK
ssh -vvv -o BatchMode=yes -o ConnectTimeout=5 h200    echo OK

# 4) Watcher heartbeat
Get-Content runtime\mesh_route_watcher_status.json | ConvertFrom-Json
```

Required invariants after the fix:

| Check                                       | Expected                                       |
|---------------------------------------------|------------------------------------------------|
| `ssh -vvv 10.43.71.7` LocalAddress          | `10.43.71.40` (ZT) — not `10.33.0.2` (tun2)   |
| `ssh -vvv 192.168.194.7` LocalAddress       | `192.168.194.39` (ZT) — not `10.33.0.2`        |
| `ssh -vvv 10.0.116.11` LocalAddress         | `outline-tap`/`OpenVPN` IPv4                    |
| Public IP via `curl https://ifconfig.me/ip` | matches `services/public_ip.py.base_ip` when Amnezia is up; otherwise shows the Amnezia egress IP |
| NetBird `100.98.x` over `wt0`               | `Find-NetRoute` resolves to `wt0`              |
| h200 over OpenVPN                           | `Find-NetRoute 10.0.116.11` → `outline-tap` family |
| WFP `Block Internet`                        | still present (do not edit WFP)                 |

---

## 15. Safety rules for AI coding agents

1. **"Анализ only" = zero mutations.** If the user says
   "только анализ" / "ничего не меняй" / "только прочитай", you may not run
   PowerShell that touches routes, WFP, the registry, services, Scheduled
   Tasks, firewall rules, or process kills. Reading via `Get-NetRoute`,
   `Get-ScheduledTask`, `Get-CimInstance`, `Get-Process`, `Get-Service`,
   `Find-NetRoute`, `Test-NetConnection`, `ssh -o BatchMode=yes ... echo OK`,
   `curl` to `ifconfig.me` / `ipapi.co` is allowed.
2. **Diagnose first.** Confirm the actual symptom (WSAEACCES, route missing,
   adapter down, ACCESS_DENIED) before any mutation.
3. **Determine the intended interface.** NetBird → `wt0`. ZeroTier cds_team →
   `ZeroTier One [93c72639168b9551]`. ZeroTier network_home →
   `ZeroTier One [60ee7c034a970d9d]`. ZT 172 → `ZeroTier One [0cccb752f7a913d8]`
   (often `ACCESS_DENIED` — that is current). OpenVPN h200 →
   `outline-tap` / `OpenVPN` / `TAP-Windows`.
4. **Verify a good path exists before deleting a hijack.** The watcher
   enforces this; manual fixes must too.
5. **Backup before mutation.** `Get-NetRoute | Export-CliXml`,
   `netsh wfp show filters > before.xml`, `reg export` for any registry
   branch you touch.
6. **Minimal change.** Prefer one route change or one Amnezia UI entry over
   cascading edits.
7. **Verify actual source address.** `ssh -vvv` shows the local address; do
   not trust `Get-NetRoute` alone.
8. **Verify unrelated networks.** Make sure h200 still routes via OpenVPN
   TAP, NetBird still via `wt0`, ZT 172 still via its adapter (or correctly
   absent). `Block Internet` must still be present in WFP.
9. **Do not declare success on `Get-NetRoute` alone.** Always finish with a
   real SSH or TCP check.
10. **Do not start/stop Amnezia / NetBird / ZeroTier.** Those are managed by
    their official apps. GPU Profiler only reads their status and (for the
    watcher) reactively removes Amnezia-introduced **physical** hijack
    routes.
11. **Amnezia `ExceptSites` is UI-only.** Never write to the registry;
    `scripts/amnezia/configure_split_tunnel.ps1` is intentionally a read-only
    helper.

---

## 16. Canonical documentation rule

```
AGENTS.md is the canonical technical memory for gpu_monitor.
Do not create additional architecture/status/progress Markdown files.
Update AGENTS.md when architecture, network topology, launchers,
watcher behaviour, or operational invariants change.
```

If you need to add a *new* doc that is not user-facing release notes, ask
first — duplication of architecture prose into other `.md` files has been the
source of every contradiction in `docs/history/`.

`docs/history/` is reserved for snapshot reports of past incidents and
migrations. New entries there must be appended; existing entries must not be
edited to retroactively change the historical record.
