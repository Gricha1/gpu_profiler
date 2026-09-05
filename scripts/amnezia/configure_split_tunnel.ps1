#Requires -Version 5.1
<#
.SYNOPSIS
  READ-ONLY helper: show suggested Amnezia split-tunnel (ExceptSites) entries.

.DESCRIPTION
  This script does NOT write the Windows Registry and does NOT modify Amnezia config.

  Amnezia stores ExceptSites as a Qt @Variant REG_BINARY value on:
    HKCU\Software\AmneziaVPN.ORG\AmneziaVPN\Conf  (value name: ExceptSites)

  Older versions of this script wrongly created a separate registry KEY
    HKCU\...\Conf\ExceptSites\
  with empty string values. Amnezia ignores that KEY. Do not recreate it.

  Add / edit split-tunnel exceptions ONLY in the AmneziaVPN UI
  (VpnAllExceptSites / sites exceptions), then Disconnect then Connect.

  Reference list (human-readable): config\amnezia_except_sites.txt
#>
$ErrorActionPreference = 'Stop'
$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path (Join-Path $Here '..\..')).Path
$ListFile = Join-Path $Root 'config\amnezia_except_sites.txt'

Write-Host '=== Amnezia split-tunnel helper (READ-ONLY) ==='
Write-Host 'This script never writes Registry, WFP, or routes.'
Write-Host 'Add ExceptSites only via AmneziaVPN UI, then Disconnect → Connect.'
Write-Host ''

if (-not (Test-Path $ListFile)) {
  Write-Host "Reference list missing: $ListFile"
  exit 1
}

$sites = Get-Content $ListFile |
  ForEach-Object { $_.Trim() } |
  Where-Object { $_ -and -not $_.StartsWith('#') }

Write-Host "Suggested entries from: $ListFile"
Write-Host ("Count: {0}" -f $sites.Count)
foreach ($s in $sites) { Write-Host "  - $s" }

Write-Host ''
Write-Host 'Active Amnezia ExceptSites = REG_BINARY on Conf (not a subkey).'
Write-Host 'Do not create Conf\ExceptSites as a registry KEY.'
exit 0
