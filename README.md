# GPU Profiler

Live GPU / RAM fleet dashboard for lab SSH hosts, plus a ChatGPT selective
PAC proxy and a read-only view of Amnezia / NetBird / ZeroTier / public-IP
status.

For architecture, SSH topology, network invariants, watcher behaviour, and
AI-agent rules, see **[AGENTS.md](./AGENTS.md)**. This README only covers
what a human user needs to install and run the project.

## Requirements

- Windows 10/11 with PowerShell 5.1+
- Python 3.10+ with `pip install -r requirements.txt`
- OpenSSH client on `PATH` (`ssh -V`)
- `nvidia-smi` on the remote hosts (and optionally on this PC)
- For the **Mesh Watcher**: admin shell once (installs a Scheduled Task)
- For the **ChatGPT PAC proxy**: `sing-box.exe` and `config-chatgpt.json`
  under `~/.ssh/timeweb-vpn/` (outside the repo, intentionally — secrets stay
  off GitHub)
- Optional: Cursor SDK key in `.env` as `CURSOR_API_KEY=...`

## Install

```powershell
cd C:\Grisha\mipt\asp\NIR\servers\gpu_monitor
pip install -r requirements.txt

# Build the two EXE launchers and create Desktop shortcuts
powershell -NoProfile -ExecutionPolicy Bypass -File launchers\install_desktop_launchers.ps1

# (Once, admin shell) install the Mesh Route Watcher Scheduled Task
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\amnezia\install_mesh_route_watcher_task.ps1
```

## Run

| Action                | How                                                                            |
|-----------------------|--------------------------------------------------------------------------------|
| Start backend + open  | Double-click **GPU Profiler** on the Desktop                                   |
| Start backend only    | `powershell -NoProfile -File scripts\start.ps1`                                |
| Stop backend          | `powershell -NoProfile -File scripts\stop.ps1` (PID-safe; does not touch proxy / Amnezia / ZT / NetBird) |
| Start Mesh Watcher    | Double-click **Mesh Watcher** on the Desktop                                   |
| ChatGPT proxy toggle  | Network card in the UI; or `scripts\proxy\start_chatgpt_proxy.ps1` / `stop_chatgpt_proxy.ps1` |

URL: **http://127.0.0.1:8765/**

## Config files

| File                  | Purpose                                                                |
|-----------------------|------------------------------------------------------------------------|
| `host_paths.json`     | Per-host SSH/TCP/AnyDesk paths; `prefer_for_probe` selects the metrics path |
| `protected_nets.json` | ZeroTier nwids and prefixes the watcher must never steal              |
| `projects.json`       | Remote project roots per host                                          |
| `.env`                | `CURSOR_API_KEY` (gitignored). Template: `.env.example`               |

Outside the repo (must stay outside): `~/.ssh/config`, `~/.ssh/timeweb-vpn/`.

## Logs / runtime state

| Path                                       | Purpose                                              |
|--------------------------------------------|------------------------------------------------------|
| `logs\uvicorn.out.log` / `uvicorn.err.log`  | Backend logs                                          |
| `logs\gpu_profiler.pid`                    | Backend listener PID                                  |
| `logs\public_ip_state.json`                | Last-known direct-egress IP                           |
| `logs\mesh_route_watcher.log`              | Watcher log                                           |
| `runtime\mesh_route_watcher_status.json`   | Watcher heartbeat (read by UI)                        |

## Diagnostics

- UI down → `scripts\start.ps1`, then `logs\uvicorn.err.log`
- Proxy down → Network card → Start, or `scripts\proxy\start_chatgpt_proxy.ps1`
- SSH host unreachable → `/api/network/mesh-health`; check ZeroTier / NetBird,
  `ssh <alias>`, `host_paths.json`
- Public IP / mode → `/api/network/public-ip` (45s cache)

## Documentation

- **Architecture, topology, AI-agent rules:** [AGENTS.md](./AGENTS.md)
- **Historical incident reports:** [docs/history/](./docs/history/)
