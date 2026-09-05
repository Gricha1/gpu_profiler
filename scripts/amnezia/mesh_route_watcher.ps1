#Requires -Version 5.1
<#
.SYNOPSIS
  Background watcher after Amnezia Connect / LAN gateway changes.

.DESCRIPTION
  Two separate jobs (no WFP / kill-switch changes):

  A) MESH ROUTE FIX — remove Wi-Fi hijacks so:
     NetBird 100.98.* -> wt0
     ZeroTier 10.43.71.* -> ZeroTier
     h200 10.0.116.* -> OpenVPN TAP

  B) DIRECT SITE ROUTE REFRESH — VK/Yandex CIDRs via CURRENT LAN/Wi-Fi gateway
     (fix stale NextHop after Wi-Fi roam)

  Never deletes Amnezia "Block Internet" / kill-switch filters.
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
$MeshFixScript = Join-Path $Root 'scripts\amnezia\fix_mesh_routes.ps1'
$DirectFixScript = Join-Path $Root 'scripts\amnezia\fix_direct_site_routes.ps1'
$LogDir = Join-Path $Root 'logs'
$RuntimeDir = Join-Path $Root 'runtime'
$LogFile = Join-Path $LogDir 'mesh_route_watcher.log'
$StatusFile = Join-Path $RuntimeDir 'mesh_route_watcher_status.json'
$PidFile = Join-Path $RuntimeDir 'mesh_route_watcher.pid'
$MutexName = 'Global\GPUProfilerMeshRouteWatcher'

New-Item -ItemType Directory -Force -Path $LogDir, $RuntimeDir | Out-Null

$created = $false
$mutex = New-Object System.Threading.Mutex($false, $MutexName, [ref]$created)
if (-not $mutex.WaitOne(0)) {
  Write-Host 'Another mesh_route_watcher instance is already running. Exit.'
  exit 0
}

. $MeshFixScript -AsLibrary
. $DirectFixScript -AsLibrary

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
    running              = $true
    pid                  = $PID
    ts                   = (Get-Date).ToString('o')
    last_check           = $Fields['last_check']
    last_fix             = $Fields['last_fix']
    last_event           = $Fields['last_event']
    amnezia_up           = [bool]$Fields['amnezia_up']
    openvpn_up           = [bool]$Fields['openvpn_up']
    netbird_route        = $Fields['netbird_route']
    zerotier_route       = $Fields['zerotier_route']
    h200_route           = $Fields['h200_route']
    lan_gateway          = $Fields['lan_gateway']
    lan_iface            = $Fields['lan_iface']
    vk_route             = $Fields['vk_route']
    yandex_route         = $Fields['yandex_route']
    last_gateway_change  = $Fields['last_gateway_change']
    last_error           = $Fields['last_error']
    last_removed         = $Fields['last_removed']
  }
  $json = ($obj | ConvertTo-Json -Compress)
  $tmp = "$StatusFile.tmp"
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($tmp, $json, $utf8)
  Move-Item -LiteralPath $tmp -Destination $StatusFile -Force
}

function Test-AmneziaTunnelUp {
  $tun = Get-NetAdapter -Name 'tun2' -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' }
  return [bool]$tun
}

function Test-OpenVpnTapUp {
  $tap = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
    $_.Status -eq 'Up' -and $_.Name -match 'outline-tap|OpenVPN|TAP-Windows'
  }
  return [bool]$tap
}

function Update-RouteStatusFields([hashtable]$State) {
  try {
    $st = Get-MeshRouteStatus
    $State.netbird_route = $st.netbird_route
    $State.zerotier_route = $st.zerotier_route
    $State.h200_route = $st.h200_route
  } catch {}
  try {
    $ds = Get-DirectSiteRouteStatus
    $State.lan_gateway = $ds.lan_gateway
    $State.lan_iface = $ds.lan_iface
    $State.vk_route = $ds.vk_route
    $State.yandex_route = $ds.yandex_route
  } catch {}
}

$state = @{
  last_check          = $null
  last_fix            = $null
  last_event          = 'started'
  amnezia_up          = $false
  openvpn_up          = $false
  netbird_route       = 'unknown'
  zerotier_route      = 'unknown'
  h200_route          = 'unknown'
  lan_gateway         = $null
  lan_iface           = $null
  vk_route            = 'unknown'
  yandex_route        = 'unknown'
  last_gateway_change = $null
  last_error          = $null
  last_removed        = 0
}

Set-Content -LiteralPath $PidFile -Value $PID -Encoding ASCII
Write-WatcherLog ("watcher start pid={0} root={1} (no kill-switch changes)" -f $PID, $Root)

$prevUp = $false
$prevOvpn = $false
$prevLanGw = $null
$lastSafety = [DateTime]::MinValue
$lastHeartbeat = [DateTime]::MinValue
$first = $true

try {
  while ($true) {
    $up = Test-AmneziaTunnelUp
    $ovpn = Test-OpenVpnTapUp
    $lan = Get-CurrentLanGateway
    $lanGw = if ($lan) { $lan.NextHop } else { $null }
    $now = Get-Date
    $doMesh = $false
    $doDirect = $false
    $reason = $null

    if ($first) {
      $first = $false
      $prevUp = $up
      $prevOvpn = $ovpn
      $prevLanGw = $lanGw
      if ($up) {
        $doMesh = $true
        $doDirect = $true
        $reason = 'initial Amnezia tunnel already up'
      } else {
        Write-WatcherLog 'Amnezia tunnel down — idle'
        $state.last_event = 'idle_amnezia_down'
      }
    } elseif ($up -and -not $prevUp) {
      $doMesh = $true
      $doDirect = $true
      $reason = 'Amnezia Connect detected'
      Write-WatcherLog 'Amnezia tunnel detected (Connect)'
      $state.last_event = 'connect'
    } elseif ($up -and $ovpn -and -not $prevOvpn) {
      $doMesh = $true
      $reason = 'OpenVPN TAP Connect detected'
      Write-WatcherLog 'OpenVPN TAP detected — mesh/h200 check only'
      $state.last_event = 'openvpn_connect'
    } elseif ($up -and $lanGw -and $prevLanGw -and ($lanGw -ne $prevLanGw)) {
      $doDirect = $true
      $reason = "LAN gateway changed $prevLanGw -> $lanGw"
      Write-WatcherLog $reason
      $state.last_event = 'lan_gw_change'
      $state.last_gateway_change = $now.ToString('o')
    } elseif (-not $up -and $prevUp) {
      Write-WatcherLog 'Amnezia tunnel down (Disconnect) — no route changes'
      $state.last_event = 'disconnect'
      $state.last_error = $null
    } elseif ($up -and (($now - $lastSafety).TotalSeconds -ge $SafetyCheckSeconds)) {
      $lastSafety = $now
      $state.last_check = $now.ToString('o')
      $hijacks = @(Get-MeshHijackRoutes)
      if ($hijacks.Count -gt 0) {
        $doMesh = $true
        $reason = ('safety: {0} mesh hijack(s)' -f $hijacks.Count)
      }
      $stale = @(Get-DirectSiteStaleRoutes -Lan $lan)
      if ($stale.Count -gt 0) {
        $doDirect = $true
        if ($reason) { $reason = "$reason + stale direct-site gw" }
        else { $reason = ('safety: {0} stale direct-site route(s)' -f $stale.Count) }
      }
    }

    if ($doMesh -or $doDirect) {
      try {
        if ($reason -like '*Connect*' -or $reason -like 'initial*' -or $reason -like '*gateway changed*') {
          Start-Sleep -Seconds $SettleSeconds
        }

        if ($doMesh) {
          Write-WatcherLog ("MESH fix ($reason)")
          $fix = Invoke-MeshRouteFix -Quiet
          $state.last_check = (Get-Date).ToString('o')
          $state.last_removed = @($fix.removed).Count
          foreach ($w in @($fix.warnings)) { Write-WatcherLog $w }
          foreach ($r in @($fix.removed)) {
            Write-WatcherLog ("mesh removed: {0} {1} -> {2} -> {3}" -f $r.Target, $r.DestinationPrefix, $r.InterfaceAlias, $r.NextHop)
          }
          if (@($fix.removed).Count -gt 0) {
            $state.last_fix = (Get-Date).ToString('o')
            Write-WatcherLog ('mesh fix complete removed={0}' -f $fix.removed.Count)
          } else {
            Write-WatcherLog 'mesh: no hijack routes'
          }
        }

        if ($doDirect) {
          Write-WatcherLog ("DIRECT-SITE refresh ($reason)")
          $dr = Invoke-DirectSiteRouteRefresh -Quiet
          foreach ($r in @($dr.removed)) {
            Write-WatcherLog ("direct removed stale: {0} via {1}" -f $r.DestinationPrefix, $r.NextHop)
          }
          foreach ($c in @($dr.added)) {
            Write-WatcherLog ("direct added: {0} via {1}" -f $c, $dr.lan.NextHop)
          }
          if ((@($dr.removed).Count + @($dr.added).Count) -gt 0) {
            $state.last_fix = (Get-Date).ToString('o')
          }
          Write-WatcherLog ('direct-site done removed={0} added={1}' -f @($dr.removed).Count, @($dr.added).Count)
        }

        Update-RouteStatusFields -State $state
        $state.last_error = $null
        $lastSafety = Get-Date
      } catch {
        $state.last_error = $_.Exception.Message
        Write-WatcherLog ("ERROR: {0}" -f $state.last_error)
      }
    } elseif ($up) {
      if (($now - $lastHeartbeat).TotalSeconds -ge $HeartbeatSeconds) {
        $state.last_check = $now.ToString('o')
        Update-RouteStatusFields -State $state
      }
    }

    if (($now - $lastHeartbeat).TotalSeconds -ge $HeartbeatSeconds) {
      $state.amnezia_up = $up
      $state.openvpn_up = $ovpn
      if ($lanGw) {
        $state.lan_gateway = $lanGw
        $state.lan_iface = $lan.InterfaceAlias
      }
      Write-StatusFile -Fields $state
      $lastHeartbeat = $now
    }

    $prevUp = $up
    $prevOvpn = $ovpn
    if ($lanGw) { $prevLanGw = $lanGw }
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
      openvpn_up = $false
      netbird_route = $state.netbird_route
      zerotier_route = $state.zerotier_route
      h200_route = $state.h200_route
      lan_gateway = $state.lan_gateway
      lan_iface = $state.lan_iface
      vk_route = $state.vk_route
      yandex_route = $state.yandex_route
      last_gateway_change = $state.last_gateway_change
      last_error = $state.last_error
      last_removed = $state.last_removed
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($StatusFile, ($dead | ConvertTo-Json -Compress), $utf8)
  } catch {}
  Remove-Item -LiteralPath $PidFile -Force -EA SilentlyContinue
  Write-WatcherLog 'watcher stopped'
  try { $mutex.ReleaseMutex() } catch {}
  try { $mutex.Dispose() } catch {}
}
