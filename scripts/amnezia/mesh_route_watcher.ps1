#Requires -Version 5.1
<#
.SYNOPSIS
  Background watcher: after Amnezia Connect, remove mesh Wi-Fi hijack routes.

.DESCRIPTION
  - Single-instance (named mutex)
  - On Amnezia tunnel rising edge: wait settle, run fix once
  - While Amnezia up: rare safety check (~45s)
  - On Disconnect: no route changes
  - Writes runtime/mesh_route_watcher_status.json heartbeat
  - Log: logs/mesh_route_watcher.log (rotated)
#>
[CmdletBinding()]
param(
  [int]$SettleSeconds = 3,
  [int]$PollMs = 2000,
  [int]$SafetyCheckSeconds = 45,
  [int]$HeartbeatSeconds = 10,
  [int]$LogMaxBytes = 2097152
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$FixScript = Join-Path $Root 'scripts\amnezia\fix_mesh_routes.ps1'
$LogDir = Join-Path $Root 'logs'
$RuntimeDir = Join-Path $Root 'runtime'
$LogFile = Join-Path $LogDir 'mesh_route_watcher.log'
$StatusFile = Join-Path $RuntimeDir 'mesh_route_watcher_status.json'
$PidFile = Join-Path $RuntimeDir 'mesh_route_watcher.pid'
$MutexName = 'Global\GPUProfilerMeshRouteWatcher'

New-Item -ItemType Directory -Force -Path $LogDir, $RuntimeDir | Out-Null

# Single instance
$created = $false
$mutex = New-Object System.Threading.Mutex($false, $MutexName, [ref]$created)
if (-not $mutex.WaitOne(0)) {
  Write-Host 'Another mesh_route_watcher instance is already running. Exit.'
  exit 0
}

. $FixScript -AsLibrary

function Write-WatcherLog([string]$Message) {
  try {
    if ((Test-Path -LiteralPath $LogFile) -and ((Get-Item -LiteralPath $LogFile).Length -gt $LogMaxBytes)) {
      $bak = "$LogFile.1"
      Remove-Item -LiteralPath $bak -Force -EA SilentlyContinue
      Move-Item -LiteralPath $LogFile -Destination $bak -Force
    }
  } catch {}
  $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
  Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Write-StatusFile([hashtable]$Fields) {
  $obj = [ordered]@{
    running         = $true
    pid             = $PID
    ts              = (Get-Date).ToString('o')
    last_check      = $Fields['last_check']
    last_fix        = $Fields['last_fix']
    last_event      = $Fields['last_event']
    amnezia_up      = [bool]$Fields['amnezia_up']
    netbird_route   = $Fields['netbird_route']
    zerotier_route  = $Fields['zerotier_route']
    last_error      = $Fields['last_error']
    last_removed    = $Fields['last_removed']
  }
  $json = ($obj | ConvertTo-Json -Compress)
  $tmp = "$StatusFile.tmp"
  # UTF-8 without BOM (Python json + utf-8-sig both OK)
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($tmp, $json, $utf8)
  Move-Item -LiteralPath $tmp -Destination $StatusFile -Force
}

function Test-AmneziaTunnelUp {
  $tun = Get-NetAdapter -Name 'tun2' -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' }
  return [bool]$tun
}

$state = @{
  last_check     = $null
  last_fix       = $null
  last_event     = 'started'
  amnezia_up     = $false
  netbird_route  = 'unknown'
  zerotier_route = 'unknown'
  last_error     = $null
  last_removed   = 0
}

Set-Content -LiteralPath $PidFile -Value $PID -Encoding ASCII
Write-WatcherLog ("watcher start pid={0} root={1}" -f $PID, $Root)

$prevUp = $false
$lastSafety = [DateTime]::MinValue
$lastHeartbeat = [DateTime]::MinValue
$first = $true

try {
  while ($true) {
    $up = Test-AmneziaTunnelUp
    $now = Get-Date
    $doFix = $false
    $reason = $null

    if ($first) {
      $first = $false
      $prevUp = $up
      if ($up) {
        $doFix = $true
        $reason = 'initial Amnezia tunnel already up'
      } else {
        Write-WatcherLog 'Amnezia tunnel down — idle'
        $state.last_event = 'idle_amnezia_down'
      }
    } elseif ($up -and -not $prevUp) {
      $doFix = $true
      $reason = 'Amnezia Connect detected'
      Write-WatcherLog 'Amnezia tunnel detected (Connect)'
      $state.last_event = 'connect'
    } elseif (-not $up -and $prevUp) {
      Write-WatcherLog 'Amnezia tunnel down (Disconnect) — no route changes'
      $state.last_event = 'disconnect'
      $state.last_error = $null
    } elseif ($up -and (($now - $lastSafety).TotalSeconds -ge $SafetyCheckSeconds)) {
      # Quiet safety: only fix if hijacks present
      $hijacks = @(Get-MeshHijackRoutes)
      $lastSafety = $now
      $state.last_check = $now.ToString('o')
      if ($hijacks.Count -gt 0) {
        $doFix = $true
        $reason = ('safety check found {0} hijack(s)' -f $hijacks.Count)
      }
    }

    if ($doFix) {
      try {
        if ($reason -like '*Connect*' -or $reason -like 'initial*') {
          Start-Sleep -Seconds $SettleSeconds
        }
        Write-WatcherLog ("mesh route check ($reason)")
        $fix = Invoke-MeshRouteFix -Quiet
        $state.last_check = (Get-Date).ToString('o')
        $state.last_removed = @($fix.removed).Count
        foreach ($w in @($fix.warnings)) { Write-WatcherLog $w }
        foreach ($r in @($fix.removed)) {
          Write-WatcherLog ("removed: {0} {1} -> {2} -> {3}" -f $r.Target, $r.DestinationPrefix, $r.InterfaceAlias, $r.NextHop)
        }
        if (@($fix.removed).Count -gt 0) {
          $state.last_fix = (Get-Date).ToString('o')
          Write-WatcherLog ('fix complete removed={0}' -f $fix.removed.Count)
        } elseif ($reason -like '*Connect*' -or $reason -like 'initial*') {
          Write-WatcherLog 'no hijack routes'
        }
        if ($fix.status_after) {
          $state.netbird_route = $fix.status_after.netbird_route
          $state.zerotier_route = $fix.status_after.zerotier_route
          foreach ($d in @($fix.status_after.details)) {
            if ($d.state -eq 'ok') {
              Write-WatcherLog ("preserved: {0} {1} -> {2}" -f $d.target, $d.probe, $d.via)
            }
          }
        }
        $state.last_error = $null
        $lastSafety = Get-Date
      } catch {
        $state.last_error = $_.Exception.Message
        Write-WatcherLog ("ERROR: {0}" -f $state.last_error)
      }
    } elseif ($up) {
      # Refresh route status lightly on heartbeat cadence only
      if (($now - $lastHeartbeat).TotalSeconds -ge $HeartbeatSeconds) {
        try {
          $st = Get-MeshRouteStatus
          $state.netbird_route = $st.netbird_route
          $state.zerotier_route = $st.zerotier_route
          $state.last_check = $now.ToString('o')
        } catch {}
      }
    }

    if (($now - $lastHeartbeat).TotalSeconds -ge $HeartbeatSeconds) {
      $state.amnezia_up = $up
      Write-StatusFile -Fields $state
      $lastHeartbeat = $now
    }

    $prevUp = $up
    Start-Sleep -Milliseconds $PollMs
  }
} finally {
  try {
    $dead = [ordered]@{
      running = $false
      pid = $PID
      ts = (Get-Date).ToString('o')
      last_check = $state.last_check
      last_fix = $state.last_fix
      last_event = 'stopped'
      amnezia_up = $false
      netbird_route = $state.netbird_route
      zerotier_route = $state.zerotier_route
      last_error = $state.last_error
      last_removed = $state.last_removed
    }
    ($dead | ConvertTo-Json -Compress) | Set-Content -LiteralPath $StatusFile -Encoding UTF8
  } catch {}
  Remove-Item -LiteralPath $PidFile -Force -EA SilentlyContinue
  Write-WatcherLog 'watcher stopped'
  try { $mutex.ReleaseMutex() } catch {}
  try { $mutex.Dispose() } catch {}
}
