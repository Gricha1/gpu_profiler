# MIGRATION_REPORT

## 1. Where the project lived before

`C:\Grisha\mipt\asp\NIR\servers\gpu_monitor`  
Companion VPN/proxy runtime: `C:\Users\User\.ssh\timeweb-vpn`  
Desktop Amnezia / Timeweb shortcuts pointed at scripts under `timeweb-vpn`.

## 2. Where it lives now

**Canonical GPU Profiler root (unchanged path, reorganized contents):**  
`C:\Grisha\mipt\asp\NIR\servers\gpu_monitor`

VPN secrets/binaries remain outside the repo in `~/.ssh/timeweb-vpn` by design.

## 3. Files transferred / added

### Added in-repo

- `services/public_ip.py` — cached public IP + mode detection
- `services/proxy_manager.py` — ChatGPT proxy control API helper
- `scripts/start.ps1`, `scripts/stop.ps1`, `scripts/create_desktop_shortcut.ps1`
- `scripts/proxy/start_chatgpt_proxy.ps1`, `scripts/proxy/stop_chatgpt_proxy.ps1` (PID-safe)
- `README.md`, `CURRENT_ARCHITECTURE.md`, `MIGRATION_REPORT.md`, `TEST_REPORT.md`
- `.env.example`, expanded `.gitignore`, `logs/`, `config/examples/`
- Desktop: `GPU Profiler.lnk`, `GPU Profiler Stop.lnk`

### UI / API changes

- `GET /` serves UI
- `GET /api/network/public-ip`
- Amnezia / Full TUN / NetBird **control endpoints disabled** (read-only status remains)
- Network card: ChatGPT Start/Stop + Public IP block + Amnezia/NetBird read-only

## 4. Legacy discovered

### ACTIVE

- GPU Profiler FastAPI on :8765
- ChatGPT selective proxy (`sing-box` + `config-chatgpt.json` :10808)
- PAC via profiler `/chatgpt.pac` (+ fallback :18080)
- AmneziaVPN installed app
- ZeroTier, NetBird
- `host_paths.json` / `projects.json` / `protect_routes.ps1`

### LEGACY (backed up under `backup/legacy/timeweb-vpn/`)

- Old `start-chatgpt-proxy.ps1` / `stop-chatgpt-proxy.ps1` that did `Get-Process sing-box | Stop-Process -Force`
- `start-vpn.ps1`, `start-vpn-tun.ps1`, `start-vpn-full.ps1` Full TUN paths
- `browser-proxy.pac`, `timeweb_socks_proxy.py`
- Copies of Amnezia start/stop/toggle scripts (still present in `timeweb-vpn` for Desktop shortcuts; GPU Profiler no longer calls them)

### UNKNOWN (not deleted)

- `~/.ssh/timeweb-vpn` revive-bore / client.pid / sb-*.err logs
- Desktop `VPN_для_S25/`
- Older Amnezia installer under `C:\Grisha\apps\VPN\`
- ITES `plot_ui.py` also defaults to port 8765 (conflict risk if both run)

## 5. Deleted / archived

- Dangerous mass-kill proxy scripts replaced by wrappers → project PID-safe scripts
- Originals copied to `backup/legacy/timeweb-vpn/` (gitignored)
- Amnezia control removed from GPU Profiler UI (app not uninstalled)

## 6. Proxy architecture changes

- Canonical start/stop live in `scripts/proxy/`
- `~/.ssh/timeweb-vpn/start|stop-chatgpt-proxy.ps1` are thin wrappers
- Stop kills only PID whose cmdline contains `config-chatgpt.json`
- PAC still preferred from `:8765/chatgpt.pac`

## 7. GPU monitoring changes

- No rewrite of SSH probe core
- Confirmed: background refresh, semaphore(3), timeouts, placeholders already present
- Added `/` route so UI works at root URL

## 8. Remaining risks

- Base public IP while Amnezia is already up may be unknown until a direct session is observed
- Full TUN legacy scripts still exist on disk (archived + UI-disabled)
- Desktop Amnezia shortcuts still call `timeweb-vpn` scripts (intentional; outside Profiler)
- `sing-box.exe` and SS credentials remain in `~/.ssh/timeweb-vpn` (must never be committed)
- Optional Cursor SDK needs `CURSOR_API_KEY` in `.env`
- Amnezia reconnect recreates kill-switch / may re-inject mesh hijack routes — mitigated by **Mesh Route Watcher** task

## 9. Mesh Route Watcher (added)

- Installed Scheduled Task `GPUProfiler-MeshRouteWatcher`
- Auto-removes Amnezia Wi‑Fi hijack routes for `100.98.*` / `10.43.71.*` after Connect
- Status API: `GET /api/mesh/watcher-status`
- UI Network card: read-only Mesh Route Watcher block
