#Requires -Version 5.1
<#
.SYNOPSIS
  Remove Amnezia Wi-Fi hijack routes for NetBird / ZeroTier / OpenVPN(h200).

.DESCRIPTION
  Amnezia ExceptSites adds WFP Allow Exclude, but also injects physical LAN routes
  (Wi-Fi/Ethernet -> gateway). That steals traffic from wt0 / ZeroTier / OpenVPN TAP.

  Additionally, with Amnezia tun2 (0.0.0.0/1 + 128.0.0.0/1), ZeroTier routes that use
  NextHop=<own ZT IP> lose to tun2 in Windows route selection even though /24 is more
  specific. This script rewrites those ZT routes to on-link NextHop=0.0.0.0 and ensures
  probe host /32 on-link routes so LocalAddress stays on ZeroTier (not 10.33.0.2).

  Removes ONLY physical hijack routes. Never touches:
  - default route / Amnezia /1 routes, WFP, kill-switch, VK/Yandex routes
  - NetBird wt0 routes (unless Wi-Fi hijacked)
  - OpenVPN TAP routes (unless Wi-Fi hijacked)

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
    Name              = 'ZeroTier-cds_team'
    StatusKey         = 'zerotier_route'
    PrefixStarts      = @('10.43.71.')
    GoodRoutePrefixes = @('10.43.71.')
    GoodIfaceRegex    = 'ZeroTier One \[93c72639168b9551\]'
    ProbeIp           = '10.43.71.7'
  },
  @{
    # ZT network_home (nwid 60ee7c034a970d9d) — lab path 192.168.194.7
    Name              = 'ZeroTier-network_home'
    StatusKey         = 'zerotier_home_route'
    PrefixStarts      = @('192.168.194.')
    GoodRoutePrefixes = @('192.168.194.')
    GoodIfaceRegex    = 'ZeroTier One \[60ee7c034a970d9d\]'
    ProbeIp           = '192.168.194.7'
  },
  @{
    # ZT nwid 0cccb752f7a913d8 (often ACCESS_DENIED until authorized) — host 172.24.158.182
    Name              = 'ZeroTier-17224'
    StatusKey         = 'zerotier_172_route'
    PrefixStarts      = @('172.24.158.182')
    GoodRoutePrefixes = @('172.24.')
    GoodIfaceRegex    = 'ZeroTier One \[0cccb752f7a913d8\]'
    ProbeIp           = '172.24.158.182'
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

function Test-ViaTun2 {
  param([string]$ProbeIp)
  if (-not $ProbeIp) { return $false }
  $rt = Find-NetRoute -RemoteIPAddress $ProbeIp -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $rt) { return $false }
  return ($rt.InterfaceAlias -match '^(tun\d*|tun2)$')
}

function Get-ZeroTierIfaceAddress {
  param($Target)
  $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    $_.InterfaceAlias -match $Target.GoodIfaceRegex -and
    $_.IPAddress -notmatch '^169\.254\.' -and
    $_.PrefixOrigin -ne 'WellKnown'
  } | Select-Object -First 1
  return $addr
}

function Repair-ZeroTierOnLinkRoutes {
  <#
    Rewrite ZT self-nexthop routes (NextHop=own IP) to on-link 0.0.0.0 and ensure
    ProbeIp/32 on-link. Required so Amnezia tun2 /1 does not steal sockets.
  #>
  param([switch]$WhatIf, [switch]$Quiet)

  $out = [ordered]@{ repaired = @(); warnings = @(); would_repair = @() }
  $ztTargets = @($script:MeshTargets | Where-Object { $_.Name -like 'ZeroTier*' })

  foreach ($t in $ztTargets) {
    if (-not (Test-GoodMeshPathPresent -Target $t)) { continue }

    $addr = Get-ZeroTierIfaceAddress -Target $t
    if (-not $addr) {
      $out.warnings += "WARN no ZT IPv4 for $($t.Name)"
      continue
    }
    $ifIndex = [int]$addr.InterfaceIndex
    $localIp = [string]$addr.IPAddress
    $alias = [string]$addr.InterfaceAlias

    # Prefer ZT over tun2 for source selection on this adapter (tun2 usually IM=5)
    try {
      $ifi = Get-NetIPInterface -InterfaceIndex $ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
      if ($ifi -and [int]$ifi.InterfaceMetric -gt 1) {
        if ($WhatIf) {
          $out.would_repair += [pscustomobject]@{ Target = $t.Name; Action = "metric $($ifi.InterfaceMetric)->1"; InterfaceAlias = $alias }
        } else {
          Set-NetIPInterface -InterfaceIndex $ifIndex -AddressFamily IPv4 -InterfaceMetric 1 -ErrorAction SilentlyContinue
          $out.repaired += [pscustomobject]@{ Target = $t.Name; Action = "metric->1"; InterfaceAlias = $alias }
        }
      }
    } catch {}

    $patterns = $t.GoodRoutePrefixes
    if (-not $patterns) { $patterns = $t.PrefixStarts }

    $routes = @(Get-NetRoute -AddressFamily IPv4 -InterfaceIndex $ifIndex -ErrorAction SilentlyContinue | Where-Object {
      (Test-RoutePrefixMatch -DestinationPrefix $_.DestinationPrefix -Patterns $patterns) -and
      $_.NextHop -and ($_.NextHop -eq $localIp)
    })

    foreach ($r in $routes) {
      $msg = '{0}: {1} NextHop {2} -> 0.0.0.0 on {3}' -f $t.Name, $r.DestinationPrefix, $r.NextHop, $alias
      if ($WhatIf) {
        $out.would_repair += [pscustomobject]@{ Target = $t.Name; Action = 'onlink-rewrite'; DestinationPrefix = $r.DestinationPrefix; InterfaceAlias = $alias }
        if (-not $Quiet) { Write-Host "WhatIf would repair: $msg" }
        continue
      }
      try {
        Remove-NetRoute -DestinationPrefix $r.DestinationPrefix -InterfaceIndex $ifIndex -NextHop $r.NextHop -Confirm:$false -ErrorAction Stop
        New-NetRoute -DestinationPrefix $r.DestinationPrefix -InterfaceIndex $ifIndex -NextHop '0.0.0.0' -AddressFamily IPv4 -RouteMetric ([Math]::Max(1, [int]$r.RouteMetric)) -ErrorAction Stop | Out-Null
        $out.repaired += [pscustomobject]@{ Target = $t.Name; Action = 'onlink-rewrite'; DestinationPrefix = $r.DestinationPrefix; InterfaceAlias = $alias }
        if (-not $Quiet) { Write-Host "repaired: $msg" }
      } catch {
        $out.warnings += "FAIL onlink-rewrite: $msg :: $($_.Exception.Message)"
        if (-not $Quiet) { Write-Host $out.warnings[-1] }
      }
    }

    # Ensure probe host /32 on-link (beats tun2 even if /24 flaps back to self-nexthop)
    if ($t.ProbeIp) {
      $hostPrefix = "$($t.ProbeIp)/32"
      $existing = Get-NetRoute -DestinationPrefix $hostPrefix -InterfaceIndex $ifIndex -ErrorAction SilentlyContinue | Select-Object -First 1
      $need = $false
      if (-not $existing) { $need = $true }
      elseif ($existing.NextHop -and $existing.NextHop -ne '0.0.0.0') { $need = $true }

      if ($need) {
        $msg = '{0}: ensure {1} on-link on {2}' -f $t.Name, $hostPrefix, $alias
        if ($WhatIf) {
          $out.would_repair += [pscustomobject]@{ Target = $t.Name; Action = 'ensure-host32'; DestinationPrefix = $hostPrefix; InterfaceAlias = $alias }
          if (-not $Quiet) { Write-Host "WhatIf would repair: $msg" }
        } else {
          try {
            if ($existing) {
              Remove-NetRoute -DestinationPrefix $hostPrefix -InterfaceIndex $ifIndex -NextHop $existing.NextHop -Confirm:$false -ErrorAction SilentlyContinue
              if (-not $existing.NextHop -or $existing.NextHop -eq '0.0.0.0') {
                Remove-NetRoute -DestinationPrefix $hostPrefix -InterfaceIndex $ifIndex -Confirm:$false -ErrorAction SilentlyContinue
              }
            }
            New-NetRoute -DestinationPrefix $hostPrefix -InterfaceIndex $ifIndex -NextHop '0.0.0.0' -AddressFamily IPv4 -RouteMetric 1 -ErrorAction Stop | Out-Null
            $out.repaired += [pscustomobject]@{ Target = $t.Name; Action = 'ensure-host32'; DestinationPrefix = $hostPrefix; InterfaceAlias = $alias }
            if (-not $Quiet) { Write-Host "repaired: $msg" }
          } catch {
            # race: already present on-link
            $again = Get-NetRoute -DestinationPrefix $hostPrefix -InterfaceIndex $ifIndex -ErrorAction SilentlyContinue | Where-Object { $_.NextHop -eq '0.0.0.0' }
            if (-not $again) {
              $out.warnings += "FAIL ensure-host32: $msg :: $($_.Exception.Message)"
              if (-not $Quiet) { Write-Host $out.warnings[-1] }
            }
          }
        }
      }
    }
  }

  return $out
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
    netbird_route        = 'unknown'
    zerotier_route       = 'unknown'
    zerotier_home_route  = 'unknown'
    zerotier_172_route   = 'unknown'
    h200_route           = 'unknown'
    details              = @()
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
    $tun2Steal = ($via -match '^(tun\d*|tun2)$')

    if ($hijacks.Count -gt 0) {
      $status[$key] = 'hijacked'
    } elseif ($tun2Steal -and ($t.Name -like 'ZeroTier*') -and ($good.Count -gt 0)) {
      # ZT routes exist but Amnezia tun2 wins (often self-nexthop vs /1)
      $status[$key] = 'tun2_steal'
    } elseif ($good.Count -gt 0) {
      if ($via -and ($via -match $t.GoodIfaceRegex)) {
        $status[$key] = 'ok'
      } elseif ($via -and ($via -notmatch $t.GoodIfaceRegex)) {
        $status[$key] = 'hijacked'
      } else {
        $status[$key] = 'ok'
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
    repaired     = @()
    skipped      = @()
    would_remove = @()
    would_repair = @()
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

  # ZT on-link repair (Amnezia tun2 vs self-nexthop)
  $ztFix = Repair-ZeroTierOnLinkRoutes -WhatIf:$WhatIf -Quiet:$Quiet
  $result.repaired = @($ztFix.repaired)
  $result.would_repair = @($ztFix.would_repair)
  foreach ($w in @($ztFix.warnings)) { $result.warnings += $w }

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
