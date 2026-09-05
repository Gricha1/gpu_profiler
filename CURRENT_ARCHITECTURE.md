# CURRENT_ARCHITECTURE

## Canonical project root

`C:\Grisha\mipt\asp\NIR\servers\gpu_monitor`

## Processes

| Process | Role | Managed by |
|---------|------|------------|
| `python -m uvicorn app:app --host 127.0.0.1 --port 8765` | FastAPI UI + APIs + PAC | `scripts/start.ps1` / Desktop shortcut |
| `sing-box.exe -c config-chatgpt.json` | Selective proxy :10808 | `scripts/proxy/*.ps1` / UI Start-Stop |
| optional PAC HttpListener :18080 | PAC fallback when UI down | `~/.ssh/timeweb-vpn/chatgpt-pac-server.ps1` |
| AmneziaVPN / AmneziaVPN-service / AmneziaWGTunnel* | Full VPN | Official Amnezia app only |
| ZeroTier One | Mesh | System service |
| NetBird | Mesh | System service |

## Ports

- **8765** — GPU Profiler
- **10808** — ChatGPT proxy
- **18080** — PAC fallback

## Configs

### In repo

- `host_paths.json`, `projects.json`, `protected_nets.json`
- `.env` (gitignored) / `.env.example`
- `scripts/`, `services/`, `static/`

### Outside repo (must stay outside)

- `~/.ssh/config`, private keys
- `~/.ssh/timeweb-vpn/` (sing-box.exe, config-chatgpt.json secrets, PAC file, Amnezia helper scripts)
- Installed Amnezia / ZeroTier / NetBird

## Why sing-box.exe stays outside the repo

It is a third-party binary (~45MB) plus configs that contain Shadowsocks credentials. Treating `~/.ssh/timeweb-vpn` as a local runtime dependency keeps secrets and binaries out of GitHub while GPU Profiler owns the PID-safe start/stop wrappers.

## Startup order

1. Desktop shortcut → `scripts/start.ps1`
2. If :8765 free → start uvicorn (minimized), write `logs/gpu_profiler.pid`
3. Wait until `GET /` returns
4. Open browser to http://127.0.0.1:8765/
5. ChatGPT proxy is independent — start via UI when needed

## GPU monitoring path

```
/api/metrics
  → cache (4s) / background refresh
  → asyncio.Semaphore(3)
  → OpenSSH `ssh` + pipe remote_probe.py
  → nvidia-smi CSV + procs/RAM/disk
  → local_probe for this PC
```

Dead hosts do not block the dashboard: UI gets placeholders / last cache while refresh continues in background (hard cap ~150s per full sweep).

## Network status

```
/api/network/public-ip  (45s cache, ipapi.co)
/api/vpn/status         (proxy / Amnezia / NetBird flags; control only for ChatGPT proxy)
/api/network/mesh-health
/api/mesh/watcher-status  (read-only Mesh Route Watcher)
```

## Mesh Route Watcher

**Problem:** Amnezia `VpnAllExceptSites` for `100.98.0.0/16` and `10.43.71.0/24` installs needed WFP Allow Exclude, but also injects Wi‑Fi/LAN hijack routes that steal traffic from `wt0` / ZeroTier.

**Fix (automatic):**

| Piece | Path |
|-------|------|
| Safe fix | `scripts/amnezia/fix_mesh_routes.ps1` (`-WhatIf` dry-run) |
| Watcher | `scripts/amnezia/mesh_route_watcher.ps1` |
| Install task | `scripts/amnezia/install_mesh_route_watcher_task.ps1` |
| Uninstall | `scripts/amnezia/uninstall_mesh_route_watcher_task.ps1` |
| Heartbeat | `runtime/mesh_route_watcher_status.json` |
| Log | `logs/mesh_route_watcher.log` |

- Task Scheduler name: **`GPUProfiler-MeshRouteWatcher`** (At logon, Highest, restart on failure)
- On Amnezia Connect (tun2 up rising edge): settle 3s → remove only physical LAN hijacks for NetBird/ZT
- Also on **OpenVPN TAP Connect** (while Amnezia up): remove Wi‑Fi hijack for `10.0.116.*` so **h200** stays on OpenVPN (`outline-tap0` / TAP), not Wi‑Fi
- Direct-site job: refresh stale VK/Yandex routes onto the **current** LAN/Wi‑Fi gateway (`fix_direct_site_routes.ps1`) — routes only
- Does **not** touch default route, metrics, WFP / Block Internet, Amnezia Registry (`ExceptSites` REG_BINARY), NetBird/ZT services
- Split-tunnel ExceptSites: add only via **AmneziaVPN UI** (never fake `Conf\ExceptSites` registry KEY)
- GPU Profiler UI shows watcher status read-only (no Start/Stop), including **h200 / OpenVPN**, LAN gateway, VK/Yandex route state
