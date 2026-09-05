#Requires -Version 5.1
# Re-apply protected ZeroTier / NetBird routes after VPN toggles.
# Safe to run repeatedly. Does NOT touch OpenVPN/Amnezia tunnels themselves.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CfgPath = Join-Path $Root 'protected_nets.json'
$Cli = 'C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat'

function Get-Json($path) {
  if (-not (Test-Path $path)) { return $null }
  try { return Get-Content -Raw -Path $path | ConvertFrom-Json } catch { return $null }
}

$cfg = Get-Json $CfgPath
if (-not $cfg) {
  Write-Host 'protect_routes: no protected_nets.json'
  exit 0
}

# Ensure ZT membership (join is idempotent; ACCESS_DENIED needs Central auth)
if (Test-Path $Cli) {
  foreach ($nwid in @($cfg.zerotier_nwids)) {
    if (-not $nwid) { continue }
    & $Cli join $nwid 2>$null | Out-Null
    & $Cli set $nwid allowManaged 1 2>$null | Out-Null
  }
}

# Ensure on-link / via-local routes for ZT assigned IPs matching protected prefixes
$ztAddrs = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.InterfaceAlias -match 'ZeroTier' -and $_.IPAddress -notmatch '^169\.254\.' }

foreach ($addr in $ztAddrs) {
  $ip = $addr.IPAddress
  $ifIndex = $addr.InterfaceIndex
  $pfxLen = [int]$addr.PrefixLength
  if ($pfxLen -le 0 -or $pfxLen -gt 32) { continue }
  # Build network address
  $bytes = ([Net.IPAddress]::Parse($ip)).GetAddressBytes()
  if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($bytes) }
  $val = [BitConverter]::ToUInt32($bytes, 0)
  $mask = if ($pfxLen -eq 0) { [uint32]0 } else { ([uint32]::MaxValue) -shl (32 - $pfxLen) }
  $netVal = $val -band $mask
  $netBytes = [BitConverter]::GetBytes([uint32]$netVal)
  if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($netBytes) }
  $netIp = ([Net.IPAddress]::new($netBytes)).ToString()
  $maskBytes = [BitConverter]::GetBytes([uint32]$mask)
  if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($maskBytes) }
  $maskIp = ([Net.IPAddress]::new($maskBytes)).ToString()

  # Only protect listed prefixes (match by start of netIp against protected list loosely)
  $protect = $false
  foreach ($p in @($cfg.prefixes)) {
    $base = ($p -split '/')[0]
    if ($netIp -eq $base -or $ip.StartsWith(($base -replace '\.0$','')) -or ($p -eq "$netIp/$pfxLen")) {
      $protect = $true; break
    }
    # 10.43.71.0/24 etc.
    if ($ip.StartsWith('10.43.71.') -and $p -like '10.43.71.*') { $protect = $true; break }
    if ($ip.StartsWith('192.168.194.') -and $p -like '192.168.194.*') { $protect = $true; break }
    if ($ip.StartsWith('172.24.') -and $p -like '172.24.*') { $protect = $true; break }
  }
  if (-not $protect) { continue }

  route delete $netIp mask $maskIp 2>$null | Out-Null
  route -p add $netIp mask $maskIp $ip metric 1 if $ifIndex 2>$null | Out-Null
  Write-Host "protect_routes: ensured $netIp/$pfxLen via $ip if=$ifIndex"
}

# Host route for lab_comp ZT IP if we have cds_team address
$labHost = '10.43.71.7'
$cds = $ztAddrs | Where-Object { $_.IPAddress -like '10.43.71.*' } | Select-Object -First 1
if ($cds) {
  route delete $labHost mask 255.255.255.255 2>$null | Out-Null
  route -p add $labHost mask 255.255.255.255 $cds.IPAddress metric 1 if $cds.InterfaceIndex 2>$null | Out-Null
  Write-Host "protect_routes: host $labHost via $($cds.IPAddress)"
}

# Never leave sticky 10.0.0.0/9 stealers if OpenVPN is down
$ovpn = Get-Process openvpn -ErrorAction SilentlyContinue
if (-not $ovpn) {
  Get-NetRoute -DestinationPrefix '10.0.0.0/9' -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'protect_routes: cleared orphan 10.0.0.0/9 (no openvpn)'
}

Write-Host 'protect_routes: done'
