#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path (Join-Path $Here '..\..')).Path
$ListFile = Join-Path $Root 'config\amnezia_except_sites.txt'
$Conf = 'HKCU:\Software\AmneziaVPN.ORG\AmneziaVPN\Conf'
$ExceptKey = Join-Path $Conf 'ExceptSites'

Write-Host "Root=$Root"
Write-Host "ListFile=$ListFile exists=$(Test-Path $ListFile)"

if (-not (Test-Path $Conf)) { throw "Amnezia Conf registry missing: $Conf" }
if (-not (Test-Path $ListFile)) { throw "Missing $ListFile" }

$sites = Get-Content $ListFile |
  ForEach-Object { $_.Trim() } |
  Where-Object { $_ -and -not $_.StartsWith('#') }
if (-not $sites.Count) { throw "No sites in $ListFile" }

$backupDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$backup = Join-Path $backupDir ("amnezia_conf_backup_{0:yyyyMMdd_HHmmss}.reg" -f (Get-Date))
reg export 'HKCU\Software\AmneziaVPN.ORG\AmneziaVPN\Conf' $backup /y | Out-Null
Write-Host "Backup: $backup"

New-ItemProperty -Path $Conf -Name 'sitesSplitTunnelingEnabled' -PropertyType DWord -Value 1 -Force | Out-Null
New-ItemProperty -Path $Conf -Name 'routeMode' -PropertyType DWord -Value 2 -Force | Out-Null

if (Test-Path $ExceptKey) { Remove-Item $ExceptKey -Recurse -Force }
New-Item -Path $ExceptKey -Force | Out-Null
foreach ($s in $sites) {
  New-ItemProperty -Path $ExceptKey -Name $s -PropertyType String -Value '' -Force | Out-Null
  Write-Host "  ExceptSites += $s"
}

Write-Host ("Configured OK: routeMode=2 sitesSplitTunnelingEnabled=1 count={0}" -f $sites.Count)
Write-Host 'NEXT: Disconnect then Connect in AmneziaVPN app, then ssh aicenter2'
