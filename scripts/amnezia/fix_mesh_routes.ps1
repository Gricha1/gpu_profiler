#Requires -Version 5.1
<#
.SYNOPSIS
  Remove Amnezia Wi-Fi hijack routes for NetBird / ZeroTier mesh overlays.

.DESCRIPTION
  Amnezia ExceptSites correctly adds WFP Allow Exclude for mesh CIDRs, but also
  injects physical LAN routes (Wi-Fi/Ethernet -> gateway). That steals traffic
  from wt0 / ZeroTier.

  This script removes ONLY those hijack routes. It never touches:
  - routes via wt0 / ZeroTier
  - default route, metrics, WFP, Amnezia, VK routes, other networks

.PARAMETER WhatIf
  Dry-run: list candidates, delete nothing.

.PARAMETER Quiet
  Suppress TCP probe noise (watcher mode). Still prints removals unless -WhatIf.

.PARAMETER AsLibrary
  Dot-source friendly: define functions only, do not run.

.EXAMPLE
  .\fix_mesh_routes.ps1 -WhatIf
  .\fix_mesh_routes.ps1
#>
[CmdletBinding(SupportsShouldProcess = $false)]
param(
  [switch]$WhatIf,
  [switch]$Quiet,
  [switch]$AsLibrary
)

$ErrorActionPreference = 'Continue'

# Only mesh overlays requested for auto-fix (do NOT expand to other ZT nets here).
$script:MeshTargets = @(
  @{
    Name            = 'NetBird'
    PrefixStarts    = @('100.98.')
    GoodIfaceRegex  = '^wt0$'
    ProbeIp         = '100.98.59.202'
  },
  @{
    Name            = 'ZeroTier'
    PrefixStarts    = @('10.43.71.')
    GoodIfaceRegex  = '^ZeroTier'
    ProbeIp         = '10.43.71.7'
  }
)

function Test-MeshPrefixMatch {
  param([string]$DestinationPrefix, [string[]]$Starts)
  foreach ($s in $Starts) {
    if ($DestinationPrefix.StartsWith($s)) { return $true }
  }
  return $false
}

function Test-PhysicalMeshHijack {
  <#
    Hijack = mesh destination on a physical iface with a real NextHop (LAN gw),
    and NOT on wt0 / ZeroTier.
  #>
  param($Route, $Target)

  if (-not (Test-MeshPrefixMatch -DestinationPrefix $Route.DestinationPrefix -Starts $Target.PrefixStarts)) {
    return $false
  }
  if (-not $Route.NextHop -or $Route.NextHop -eq '0.0.0.0') { return $false }
  if ($Route.InterfaceAlias -match $Target.GoodIfaceRegex) { return $false }
  # Never treat tunnel / virtual overlays as hijack targets
  if ($Route.InterfaceAlias -match '^(wt0|ZeroTier|tun\d*|Wintun|Amnezia|Tailscale|vEthernet)') {
    return $false
  }
  # Physical Wi-Fi / Ethernet / LAN-looking adapters only (ASCII patterns;
  # localized adapter names fall through to private NextHop check below).
  if ($Route.InterfaceAlias -notmatch 'Wi-?Fi|WLAN|Wireless|Ethernet|Local Area') {
    # Fallback: any non-overlay with private LAN next-hop still counts
    if ($Route.NextHop -notmatch '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)') {
      return $false
    }
  }
  return $true
}

function Test-GoodMeshPathPresent {
  param($Target)
  $goodIface = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
    $_.Status -eq 'Up' -and $_.Name -match $Target.GoodIfaceRegex
  } | Select-Object -First 1
  if (-not $goodIface) { return $false }

  $goodRoute = Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    (Test-MeshPrefixMatch -DestinationPrefix $_.DestinationPrefix -Starts $Target.PrefixStarts) -and
    ($_.InterfaceAlias -match $Target.GoodIfaceRegex)
  } | Select-Object -First 1
  return [bool]$goodRoute
}

function Get-MeshHijackRoutes {
  $all = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue)
  $hits = @()
  foreach ($t in $script:MeshTargets) {
    foreach ($r in $all) {
      if (Test-PhysicalMeshHijack -Route $r -Target $t) {
        $hits += [pscustomobject]@{
          Target            = $t.Name
          DestinationPrefix = $r.DestinationPrefix
          InterfaceAlias    = $r.InterfaceAlias
          InterfaceIndex    = $r.InterfaceIndex
          NextHop           = $r.NextHop
          RouteMetric       = $r.RouteMetric
          GoodPathPresent   = (Test-GoodMeshPathPresent -Target $t)
        }
      }
    }
  }
  return $hits
}

function Get-MeshRouteStatus {
  <#
    Returns hashtable netbird_route / zerotier_route = ok|hijacked|unknown|missing
  #>
  $status = @{
    netbird_route  = 'unknown'
    zerotier_route = 'unknown'
    details        = @()
  }
  $all = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue)
  foreach ($t in $script:MeshTargets) {
    $key = if ($t.Name -eq 'NetBird') { 'netbird_route' } else { 'zerotier_route' }
    $hijacks = @($all | Where-Object { Test-PhysicalMeshHijack -Route $_ -Target $t })
    $good = @($all | Where-Object {
      (Test-MeshPrefixMatch -DestinationPrefix $_.DestinationPrefix -Starts $t.PrefixStarts) -and
      ($_.InterfaceAlias -match $t.GoodIfaceRegex)
    })
    $rt = Find-NetRoute -RemoteIPAddress $t.ProbeIp -ErrorAction SilentlyContinue | Select-Object -First 1
    $via = if ($rt) { $rt.InterfaceAlias } else { $null }
    if ($hijacks.Count -gt 0) {
      $status[$key] = 'hijacked'
    } elseif ($good.Count -gt 0 -and $via -and ($via -match $t.GoodIfaceRegex)) {
      $status[$key] = 'ok'
    } elseif ($good.Count -gt 0 -and (-not $via -or $via -notmatch 'tun\d*')) {
      # Prefer table presence on overlay adapter even if Find-NetRoute is noisy
      $status[$key] = 'ok'
      if (-not $via) { $via = $good[0].InterfaceAlias }
    } elseif ($good.Count -gt 0) {
      # Overlay route exists; Find-NetRoute may transiently pick tun2 under Amnezia
      $status[$key] = 'ok'
      $via = $good[0].InterfaceAlias
    } elseif (-not (Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.Name -match $t.GoodIfaceRegex })) {
      $status[$key] = 'missing'
    } else {
      $status[$key] = 'unknown'
    }
    $status.details += [pscustomobject]@{
      target = $t.Name
      probe  = $t.ProbeIp
      via    = $via
      state  = $status[$key]
    }
  }
  return $status
}

function Invoke-MeshRouteFix {
  param([switch]$WhatIf, [switch]$Quiet)

  $result = [ordered]@{
    removed      = @()
    skipped      = @()
    would_remove = @()
    warnings     = @()
    status_after = $null
  }

  $candidates = @(Get-MeshHijackRoutes)
  foreach ($c in $candidates) {
    $msg = '{0}: {1} -> {2} -> {3}' -f $c.Target, $c.DestinationPrefix, $c.InterfaceAlias, $c.NextHop
    if (-not $c.GoodPathPresent) {
      $warn = "WARN skip (no good mesh path present): $msg"
      $result.warnings += $warn
      $result.skipped += $c
      if (-not $Quiet) { Write-Host $warn }
      continue
    }
    if ($WhatIf) {
      $result.would_remove += $c
      if (-not $Quiet) { Write-Host "WhatIf would remove: $msg" }
      continue
    }
    try {
      Remove-NetRoute -DestinationPrefix $c.DestinationPrefix -InterfaceIndex $c.InterfaceIndex `
        -NextHop $c.NextHop -Confirm:$false -ErrorAction Stop
      $result.removed += $c
      if (-not $Quiet) { Write-Host "removed: $msg" }
    } catch {
      try {
        $ip = $c.DestinationPrefix.Split('/')[0]
        & route.exe delete $ip 2>$null | Out-Null
        $result.removed += $c
        if (-not $Quiet) { Write-Host "removed (route.exe): $msg" }
      } catch {
        $result.warnings += "FAIL remove: $msg :: $($_.Exception.Message)"
        if (-not $Quiet) { Write-Host $result.warnings[-1] }
      }
    }
  }

  $result.status_after = Get-MeshRouteStatus
  if (-not $Quiet -and -not $WhatIf) {
    foreach ($d in $result.status_after.details) {
      Write-Host ("preserved check: {0} {1} via={2} state={3}" -f $d.target, $d.probe, $d.via, $d.state)
    }
  }
  return $result
}

if (-not $AsLibrary) {
  $null = Invoke-MeshRouteFix -WhatIf:$WhatIf -Quiet:$Quiet
}
