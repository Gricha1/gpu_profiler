# GPU Profiler

Live GPU / RAM fleet dashboard for lab SSH hosts, plus ChatGPT selective proxy controls and public IP status.

## Architecture

```
Desktop shortcut
    ↓
GPU Profiler launcher (scripts/start.ps1)
    ↓
FastAPI :8765
    ├── UI (/)
    ├── GPU monitoring (/api/metrics)
    ├── Public IP status (/api/network/public-ip)
    └── ChatGPT selective proxy manager (/api/vpn/ssh/on|off, /chatgpt.pac)

Remote GPU servers
    ↑
SSH / ZeroTier / NetBird  (OpenSSH CLI + nvidia-smi probe)

ChatGPT proxy:
Browser/PAC
    ↓
http://127.0.0.1:8765/chatgpt.pac  (fallback :18080)
    ↓
local sing-box mixed :10808
    ↓
remote VPS (Shadowsocks)

Full VPN:
AmneziaVPN
    ↓
managed independently by official Amnezia application
(GPU Profiler shows read-only tunnel status only)
```

## Quick start

1. Install deps: `pip install -r requirements.txt`
2. Create desktop shortcuts: `powershell -File scripts\create_desktop_shortcut.ps1`
3. Double-click **GPU Profiler** on the Desktop  
   — or: `powershell -File scripts\start.ps1`
4. Open http://127.0.0.1:8765/

Stop backend only: Desktop **GPU Profiler Stop** or `powershell -File scripts\stop.ps1`  
(Does **not** stop ChatGPT proxy / Amnezia / ZeroTier / NetBird.)

## Config

| File | Purpose |
|------|---------|
| `host_paths.json` | Per-host SSH/ZT/NetBird/LAN paths |
| `projects.json` | Favorite Cursor project paths |
| `protected_nets.json` | ZeroTier networks never to steal |
| `.env` | Secrets (`CURSOR_API_KEY`) — gitignored |
| `~/.ssh/config` | SSH host aliases (outside repo) |
| `~/.ssh/timeweb-vpn/` | sing-box binary + ChatGPT PAC/proxy secrets (outside repo) |

Examples without secrets: `config/examples/`.

### Add a GPU server

1. Add an SSH host alias in `~/.ssh/config`.
2. Add the host id to `HOSTS` in `app.py`.
3. Add probe paths in `host_paths.json`.
4. Optionally add project roots in `projects.json`.

## ChatGPT proxy

- **Start/Stop** from the Network card in the UI.
- Uses PID files (`sing-box-chatgpt.pid`) — never mass-kills all `sing-box` processes.
- Does not touch Amnezia, NetBird, or ZeroTier.
- PAC is served at `/chatgpt.pac` while the profiler is up.

## Amnezia

AmneziaVPN is a separate installed application. GPU Profiler **does not** start/stop it.  
UI only reports whether an `AmneziaWGTunnel*` service is running.

## Ports

| Port | Role |
|------|------|
| 8765 | GPU Profiler UI + PAC |
| 10808 | ChatGPT selective proxy (sing-box mixed) |
| 18080 | PAC fallback HTTP server (optional) |

## Logs

`logs/` — uvicorn stdout/stderr, PID file, public IP base-IP state.

## Diagnostics

- UI down: `scripts\start.ps1`, check `logs\uvicorn.err.log`
- Proxy down: UI Start, or `scripts\proxy\start_chatgpt_proxy.ps1`
- SSH hosts offline: check ZeroTier / NetBird; `ssh <alias>`; `host_paths.json`
- Public IP: `/api/network/public-ip` (45s cache)

## Requirements

See `requirements.txt`. Also needs: OpenSSH client, Python 3.10+, optional `nvidia-smi` for local GPUs.
