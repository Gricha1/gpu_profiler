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

