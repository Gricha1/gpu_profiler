#Requires -Version 5.1
<#
.SYNOPSIS
  Stop GPU Profiler backend on :8765 (PID file / listener). Does not stop ChatGPT proxy, Amnezia, ZT, NetBird.
#>
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PidFile = Join-Path $Root 'logs\gpu_profiler.pid'
$Port = 8765

function Stop-PidSafe([int]$Id) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction SilentlyContinue
  if (-not $proc) { return }
  $cmd = [string]$proc.CommandLine
  if ($cmd -notmatch 'uvicorn' -and $cmd -notmatch 'app:app' -and $proc.Name -notmatch 'python') {
    Write-Host "skip pid=$Id (not our uvicorn)"
    return
  }
  Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
  Write-Host "stopped pid=$Id"
}

if (Test-Path $PidFile) {
  $raw = (Get-Content $PidFile -Raw).Trim()
  if ($raw -match '^\d+$') { Stop-PidSafe ([int]$raw) }
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# Also stop listener on 8765 if it is python/uvicorn
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
  $id = [int]$c.OwningProcess
  if ($id -gt 4) { Stop-PidSafe $id }
}

Write-Host 'GPU Profiler stopped (proxy/Amnezia/ZT/NetBird untouched)'
