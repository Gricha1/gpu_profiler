#Requires -Version 5.1
<#
.SYNOPSIS
  Refresh direct-site Amnezia split-tunnel routes onto the CURRENT LAN/Wi-Fi gateway
  (VK/Yandex + OpenVPN Personal-2 endpoints).

.DESCRIPTION
  Does NOT touch WFP / kill-switch / Block Internet.
  Only replaces stale physical NextHop routes for configured CIDRs when the
  Wi-Fi/LAN default gateway has changed.

.PARAMETER WhatIf
  Show planned changes only.

.PARAMETER AsLibrary
  Dot-source: define functions, do not run.
#>
[CmdletBinding()]
param(
  [switch]$WhatIf,
  [switch]$Quiet,
  [switch]$AsLibrary
)

$ErrorActionPreference = 'Continue'

$script:TunnelLike = 'tun|wt0|ZeroTier|vEthernet|Loopback|Tailscale|Wintun|Amnezia|outline-tap|OpenVPN|TAP-Windows'

# Canonical covering CIDRs / hosts for Amnezia ExceptSites direct paths
$script:DirectSiteCidrs = @(
  @{ Name = 'VK';     Cidrs = @('87.240.0.0/16', '93.186.224.0/20', '95.213.0.0/16') },
  @{ Name = 'Yandex'; Cidrs = @('77.88.0.0/16', '5.255.0.0/16', '87.250.0.0/16', '93.158.0.0/16', '213.180.0.0/16') },
  # OpenVPN Personal-2 endpoints (must not nest via Amnezia tun2)
  @{ Name = 'OpenVPN'; Cidrs = @('185.178.210.151/32', '185.178.210.152/32') }
)

$script:DirectPrefixMatch = '^(87\.240\.|93\.186\.|95\.213\.|77\.88\.|5\.255\.|87\.250\.|93\.158\.|213\.180\.|185\.178\.210\.)'

function Get-CurrentLanGateway {
  <#
    Physical LAN/Wi-Fi default route (excludes tunnels/virtual overlays).
    Returns: InterfaceAlias, InterfaceIndex, NextHop  or $null
  #>
  $def = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.NextHop -ne '0.0.0.0' -and $_.InterfaceAlias -notmatch $script:TunnelLike } |
    Sort-Object RouteMetric, InterfaceMetric |
    Select-Object -First 1
  if (-not $def) { return $null }
  return [pscustomobject]@{
    InterfaceAlias = $def.InterfaceAlias
    InterfaceIndex = $def.InterfaceIndex
    NextHop        = [string]$def.NextHop
  }
}

function Get-DirectSiteStaleRoutes {
  param($Lan)
  if (-not $Lan) { return @() }
  @(Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    $_.DestinationPrefix -match $script:DirectPrefixMatch -and
    $_.NextHop -and $_.NextHop -ne '0.0.0.0' -and
    $_.InterfaceAlias -notmatch $script:TunnelLike -and
    (
      $_.NextHop -ne $Lan.NextHop -or
      $_.InterfaceIndex -ne $Lan.InterfaceIndex
    )
  })
}

function Get-DirectSiteRouteStatus {
  $lan = Get-CurrentLanGateway
  $status = @{
    lan_gateway   = if ($lan) { $lan.NextHop } else { $null }
    lan_iface     = if ($lan) { $lan.InterfaceAlias } else { $null }
    vk_route      = 'unknown'
    yandex_route  = 'unknown'
    stale_count   = 0
    details       = @()
  }
  if (-not $lan) {
    $status.vk_route = 'missing'
    $status.yandex_route = 'missing'
    return $status
  }

  $stale = @(Get-DirectSiteStaleRoutes -Lan $lan)
  $status.stale_count = $stale.Count

  foreach ($group in $script:DirectSiteCidrs) {
    $key = switch ($group.Name) {
      'VK' { 'vk_route' }
      'Yandex' { 'yandex_route' }
      default { $null }
    }
    $okCover = $false
    foreach ($c in $group.Cidrs) {
      $hit = Get-NetRoute -DestinationPrefix $c -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -eq $lan.NextHop -and $_.InterfaceIndex -eq $lan.InterfaceIndex }
      if ($hit) { $okCover = $true; break }
    }
    $groupStale = @($stale | Where-Object {
      foreach ($c in $group.Cidrs) {
        if ($_.DestinationPrefix -eq $c -or $_.DestinationPrefix.StartsWith(($c.Split('/')[0] -replace '\.0$','.'))) { return $true }
      }
      $p = $_.DestinationPrefix
      if ($group.Name -eq 'VK' -and $p -match '^(87\.240|93\.186|95\.213)') { return $true }
      if ($group.Name -eq 'Yandex' -and $p -match '^(77\.88|5\.255|87\.250|93\.158|213\.180)') { return $true }
      if ($group.Name -eq 'OpenVPN' -and $p -match '^185\.178\.210\.(151|152)') { return $true }
      return $false
    })
    $state = if ($groupStale.Count -gt 0) { 'stale' } elseif ($okCover) { 'ok' } else { 'missing' }
    if ($key) { $status[$key] = $state }
    $status.details += [pscustomobject]@{
      site  = $group.Name
      state = $state
      via   = $lan.NextHop
    }
  }
  return $status
}

function Invoke-DirectSiteRouteRefresh {
  param([switch]$WhatIf, [switch]$Quiet)

  $result = [ordered]@{
    lan            = $null
    removed        = @()
    added          = @()
    skipped        = @()
    would_remove   = @()
    would_add      = @()
    status_after   = $null
  }

  $lan = Get-CurrentLanGateway
  $result.lan = $lan
  if (-not $lan) {
    if (-not $Quiet) { Write-Host 'WARN: no physical LAN/Wi-Fi default gateway' }
    $result.status_after = Get-DirectSiteRouteStatus
    return $result
  }
  if (-not $Quiet) {
    Write-Host ("LAN gateway={0} iface={1} ifIndex={2}" -f $lan.NextHop, $lan.InterfaceAlias, $lan.InterfaceIndex)
  }

  # 1) Remove only stale physical routes (NextHop/iface != current LAN)
  foreach ($r in @(Get-DirectSiteStaleRoutes -Lan $lan)) {
    $msg = '{0} -> {1} on {2}' -f $r.DestinationPrefix, $r.NextHop, $r.InterfaceAlias
    if ($WhatIf) {
      $result.would_remove += $r
      if (-not $Quiet) { Write-Host "WhatIf remove stale: $msg" }
      continue
    }
    try {
      Remove-NetRoute -DestinationPrefix $r.DestinationPrefix -InterfaceIndex $r.InterfaceIndex `
        -NextHop $r.NextHop -Confirm:$false -ErrorAction Stop
      $result.removed += $r
      if (-not $Quiet) { Write-Host "removed stale: $msg" }
    } catch {
      try {
        & route.exe delete ($r.DestinationPrefix.Split('/')[0]) 2>$null | Out-Null
        $result.removed += $r
        if (-not $Quiet) { Write-Host "removed stale (route.exe): $msg" }
      } catch {
        if (-not $Quiet) { Write-Host "FAIL remove: $msg :: $($_.Exception.Message)" }
      }
    }
  }

  # 2) Ensure covering CIDRs exist via current gateway (do not rewrite if already OK)
  foreach ($group in $script:DirectSiteCidrs) {
    foreach ($c in $group.Cidrs) {
      $exists = Get-NetRoute -DestinationPrefix $c -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -eq $lan.NextHop -and $_.InterfaceIndex -eq $lan.InterfaceIndex }
      if ($exists) {
        $result.skipped += $c
        if (-not $Quiet) { Write-Host ("  OK {0} via {1}" -f $c, $lan.NextHop) }
        continue
      }
      if ($WhatIf) {
        $result.would_add += $c
        if (-not $Quiet) { Write-Host ("WhatIf add {0} via {1}" -f $c, $lan.NextHop) }
        continue
      }
      try {
        New-NetRoute -DestinationPrefix $c -InterfaceIndex $lan.InterfaceIndex `
          -NextHop $lan.NextHop -RouteMetric 1 -ErrorAction Stop | Out-Null
        $result.added += $c
        if (-not $Quiet) { Write-Host ("  added {0} via {1}" -f $c, $lan.NextHop) }
      } catch {
        if (-not $Quiet) { Write-Host ("FAIL add {0}: {1}" -f $c, $_.Exception.Message) }
      }
    }
  }

  $result.status_after = Get-DirectSiteRouteStatus
  return $result
}

if (-not $AsLibrary) {
  $null = Invoke-DirectSiteRouteRefresh -WhatIf:$WhatIf -Quiet:$Quiet
}
