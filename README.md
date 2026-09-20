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

## Recent UI Fixes (2026-09-19)

### GPU Stats Chart Modal
- **Legend text color**: Fixed dark text on dark background by adding `color: "#cbd5e1"` and `fontColor: "#cbd5e1"` to legend items returned by `generateLabels` callback
- **Global Chart.js defaults**: Set `Chart.defaults.color = "#cbd5e1"` for consistent light text across all chart elements
- **Cache prevention**: Added `Cache-Control: no-cache, no-store, must-revalidate` headers to index.html response to prevent browser caching issues

### Home Usage Dropdown
- **Persistent selection**: Home usage user selection now persists across page refreshes via localStorage (`home_usage_${host}`)
- **Dropdown stays open**: Modified `isProjectPickerBusy()` to also check for `.home-user-select` focus, preventing auto-refresh (every 5s) from closing the dropdown while user is selecting

### Chart Layout
- **Separate charts**: VRAM and GPU Utilization displayed in two separate charts (stacked vertically) instead of combined
- **Fixed heights**: Each chart wrapped in `.chart-wrap` div with fixed 240px height to prevent canvas sizing issues
- **Modal scrollbar**: Chart modal body has `overflow-y: auto` for scrolling when content exceeds viewport
- **Legend styling**: Filled rectangles (not empty outlines) with soft pastel colors per GPU, `generateLabels` callback for custom rendering
- **X-axis dates**: Time scale with day/hour display formats, auto-skipping ticks, max 14 ticks
