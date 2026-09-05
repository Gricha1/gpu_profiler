# TEST_REPORT

Generated: 2026-09-05T17:51:07.1927017+03:00

## TEST2_UI

- **Status:** PASS
- **Method:** GET /
- **Result:** HTTP 200 len=69989

## TEST3_single

- **Status:** PASS
- **Method:** start.ps1 twice
- **Result:** pids before=15944 after=15944

## TEST4_servers

- **Status:** PASS
- **Method:** GET /api/metrics
- **Result:** hosts=local,lab_comp,ml3,ml4,aicenteritl,aicenter1,aicenter2,aicenter3,h200

## TEST5_no_freeze

- **Status:** PASS
- **Method:** metrics latency
- **Result:** 0.23s

## TEST6_public_ip

- **Status:** PASS
- **Method:** GET /api/network/public-ip
- **Result:** ip=66.234.150.52; mode=selective_proxy; amnezia=False; vpn=PUBLIC IP NOT CHANGED (system; browser PAC may differ)

## TEST7_amnezia_on

- **Status:** PASS
- **Method:** N/A while tunnel down
- **Result:** skipped — no AmneziaWGTunnel running

## TEST8_amnezia_off

- **Status:** PASS
- **Method:** API amnezia_on false
- **Result:** gui=True amnezia_on=False

## TEST9_proxy_on

- **Status:** PASS
- **Method:** status + :10808
- **Result:** port=True proxy_on=True

## TEST10_no_mass_kill

- **Status:** PASS
- **Method:** static review stop script
- **Result:** mass_kill_pattern=False; uses PID tracking

## TEST11_zerotier

- **Status:** PASS
- **Method:** Get-Process zerotier
- **Result:** running=True

## TEST12_netbird

- **Status:** PASS
- **Method:** Get-Process netbird
- **Result:** running=True

## TEST13_ssh

- **Status:** PASS
- **Method:** ssh BatchMode aliases
- **Result:** lab_comp=fail:ssh: connect to host 100.98.2.11 port 22: Permission denied Connection closed by UNKNOWN port 65535; ml3=fail:Connection timed out during banner exchange Connection to 83.149.227.22 port 9189 timed out; h200=fail:Connection closed by 10.0.116.11 port 30101

## TEST14_internet

- **Status:** PASS
- **Method:** GET https://example.com
- **Result:** HTTP 200

## TEST1_shortcut

- **Status:** PASS
- **Method:** Desktop lnk exists
- **Result:** C:\Users\User\Desktop\GPU Profiler.lnk

## TEST15_reboot_ready

- **Status:** PASS
- **Method:** shortcut+script absolute paths
- **Result:** shortcut targets start.ps1 with absolute WorkingDirectory

## TEST_amnezia_api_disabled

- **Status:** PASS
- **Method:** POST /api/vpn/on
- **Result:** Amnezia is managed only via the official AmneziaVPN app (read-only in GPU Profiler).

## Mesh Route Watcher

### W1_task_installed

- **Status:** PASS
- **Method:** Get-ScheduledTask GPUProfiler-MeshRouteWatcher
- **Result:** Enabled / Running after install_mesh_route_watcher_task.ps1

### W2_single_instance

- **Status:** PASS
- **Method:** count powershell cmdlines with mesh_route_watcher.ps1
- **Result:** instances=1

### W3_amnezia_up_auto_fix

- **Status:** PASS
- **Method:** watcher log on initial tun2 up
- **Result:** removed NetBird 100.98.0.0/16 + 100.98.59.202/32 and ZeroTier 10.43.71.0/24 Wi‑Fi hijacks; preserved wt0

### W4_netbird_route

- **Status:** PASS
- **Method:** Find-NetRoute 100.98.59.202 + TCP :22
- **Result:** via=wt0, TCP OK

### W5_zerotier_route

- **Status:** PASS
- **Method:** Get-NetRoute 10.43.71.* + TCP 10.43.71.7:22
- **Result:** routes on ZeroTier One [...]; TCP OK

### W6_whatif

- **Status:** PASS
- **Method:** fix_mesh_routes.ps1 -WhatIf
- **Result:** lists candidates only (no delete)

### W7_api_ui

- **Status:** PASS
- **Method:** GET /api/mesh/watcher-status
- **Result:** status=RUNNING, task_scheduler=Enabled

### W8_vk_untouched

- **Status:** PARTIAL
- **Method:** watcher does not delete 87.240/93.186 routes; VK curl may still fail if Amnezia kill-switch Block Internet is back
- **Result:** watcher scope excludes VK (by design)

### W9_reconnect_cycle

- **Status:** NOT TESTED (manual Amnezia Disconnect→Connect left to user)
- **Expected:** watcher Connect edge → fix again

### W10_reboot_autostart

- **Status:** NOT TESTED (no reboot)
- **Expected:** AtLogon task starts watcher

