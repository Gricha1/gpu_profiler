#Requires -Version 5.1
<#
.SYNOPSIS
  Remove Amnezia Wi-Fi hijack routes for NetBird / ZeroTier / OpenVPN(h200).

.DESCRIPTION
  Amnezia ExceptSites adds WFP Allow Exclude, but also injects physical LAN routes
  (Wi-Fi/Ethernet -> gateway). That steals traffic from wt0 / ZeroTier / OpenVPN TAP.

  Removes ONLY those hijack routes. Never touches:
  - routes via wt0 / ZeroTier / OpenVPN TAP
  - default route, metrics, WFP, Amnezia, VK routes, other networks

.PARAMETER WhatIf
  Dry-run: list candidates, delete nothing.

.PARAMETER Quiet
  Quieter output for watcher mode.

.PARAMETER AsLibrary
  Define functions only (dot-source).
#>
[CmdletBinding(SupportsShouldProcess = $false)]
param(
  [switch]$WhatIf,
  [switch]$Quiet,
  [switch]$AsLibrary
)

$ErrorActionPreference = 'Continue'

# Targets: PrefixStarts = what Amnezia may hijack via Wi-Fi.
# GoodRoutePrefixes = destinations on the good adapter that prove a safe path exists
# (OpenVPN often covers h200 via 10.0.0.0/9, not only 10.0.116.11/32).
$script:MeshTargets = @(
  @{
    Name              = 'NetBird'
    StatusKey         = 'netbird_route'
    PrefixStarts      = @('100.98.')
    GoodRoutePrefixes = @('100.98.')
    GoodIfaceRegex    = '^wt0$'
    ProbeIp           = '100.98.59.202'
  },
  @{
    Name              = 'ZeroTier'
    StatusKey         = 'zerotier_route'
    PrefixStarts      = @('10.43.71.')
    GoodRoutePrefixes = @('10.43.71.')
    GoodIfaceRegex    = '^ZeroTier'
    ProbeIp           = '10.43.71.7'
  },
  @{
    Name              = 'OpenVPN-h200'
    StatusKey         = 'h200_route'
    PrefixStarts      = @('10.0.116.')
    GoodRoutePrefixes = @('10.0.116.', '10.0.0.0/9')
    GoodIfaceRegex    = 'outline-tap|OpenVPN|TAP-Windows'
    ProbeIp           = '10.0.116.11'
  }
)

function Test-RoutePrefixMatch {
  param([string]$DestinationPrefix, [string[]]$Patterns)
  foreach ($p in $Patterns) {
    if ($p -match '/') {
      if ($DestinationPrefix -eq $p) { return $true }
    } elseif ($DestinationPrefix.StartsWith($p)) {
      return $true
    }
  }
  return $false
}

function Test-PhysicalMeshHijack {
  param($Route, $Target)

  if (-not (Test-RoutePrefixMatch -DestinationPrefix $Route.DestinationPrefix -Patterns $Target.PrefixStarts)) {
    return $false
  }
  if (-not $Route.NextHop -or $Route.NextHop -eq '0.0.0.0') { return $false }
  if ($Route.InterfaceAlias -match $Target.GoodIfaceRegex) { return $false }
  # Never treat tunnel / virtual overlays as hijack sources to delete
  if ($Route.InterfaceAlias -match '^(wt0|ZeroTier|tun\d*|Wintun|Amnezia|Tailscale|vEthernet|outline-tap|OpenVPN|TAP-Windows)') {
    return $false
  }
  # Physical Wi-Fi / Ethernet / LAN-looking adapters (ASCII); else private NextHop fallback
  if ($Route.InterfaceAlias -notmatch 'Wi-?Fi|WLAN|Wireless|Ethernet|Local Area') {
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

  $patterns = $Target.GoodRoutePrefixes
  if (-not $patterns) { $patterns = $Target.PrefixStarts }

  $goodRoute = Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    ($_.InterfaceAlias -match $Target.GoodIfaceRegex) -and
    (Test-RoutePrefixMatch -DestinationPrefix $_.DestinationPrefix -Patterns $patterns)
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
          StatusKey         = $t.StatusKey
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
  $status = @{
    netbird_route  = 'unknown'
    zerotier_route = 'unknown'
    h200_route     = 'unknown'
    details        = @()
  }
  $all = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue)
  foreach ($t in $script:MeshTargets) {
    $key = $t.StatusKey
    $patterns = $t.GoodRoutePrefixes
    if (-not $patterns) { $patterns = $t.PrefixStarts }

    $hijacks = @($all | Where-Object { Test-PhysicalMeshHijack -Route $_ -Target $t })
    $good = @($all | Where-Object {
      ($_.InterfaceAlias -match $t.GoodIfaceRegex) -and
      (Test-RoutePrefixMatch -DestinationPrefix $_.DestinationPrefix -Patterns $patterns)
    })
    $rt = Find-NetRoute -RemoteIPAddress $t.ProbeIp -ErrorAction SilentlyContinue | Select-Object -First 1
    $via = if ($rt) { $rt.InterfaceAlias } else { $null }

    if ($hijacks.Count -gt 0) {
      $status[$key] = 'hijacked'
    } elseif ($good.Count -gt 0) {
      $status[$key] = 'ok'
      if (-not $via -or ($via -notmatch $t.GoodIfaceRegex)) {
        $via = $good[0].InterfaceAlias
      }
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
      $warn = "WARN skip (no good overlay/OpenVPN path present): $msg"
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
