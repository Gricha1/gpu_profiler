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
```
