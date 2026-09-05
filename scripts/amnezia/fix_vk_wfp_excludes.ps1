#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Fix VK "доступ заблокирован" with Amnezia Full VPN + site excludes.

.DESCRIPTION
  Amnezia installs Wi-Fi exclude routes for ExceptSites but often does NOT create
  matching WFP "Allow Ecxlude" permits. Kill-switch ("Block Internet") then blocks
  those paths -> browser shows firewall/antivirus block page.

  This script:
    1) Removes Amnezia "Block Internet" WFP filters (kill-switch off for this session)
    2) Ensures LAN routes for VK CIDRs

  NOTE: Amnezia Disconnect/Connect will recreate Block Internet — re-run this script
  after reconnect if VK breaks again.
#>
$ErrorActionPreference = 'Stop'

$Cidrs = @('87.240.0.0/16', '93.186.224.0/20', '95.213.0.0/16')

$cs = @'
using System;
using System.Runtime.InteropServices;
public static class WfpKillSwitchOff {
  [DllImport("fwpuclnt.dll", EntryPoint="FwpmEngineOpen0")]
  public static extern uint Open(IntPtr s, uint a, IntPtr i, IntPtr sess, out IntPtr e);
  [DllImport("fwpuclnt.dll")] public static extern uint FwpmEngineClose0(IntPtr e);
  [DllImport("fwpuclnt.dll")] public static extern uint FwpmFilterDeleteById0(IntPtr e, ulong id);
  public static string Del(ulong id) {
    IntPtr e; uint r = Open(IntPtr.Zero, 0xFFFFFFFF, IntPtr.Zero, IntPtr.Zero, out e);
    if (r != 0) return "open=0x" + r.ToString("X");
    try {
      r = FwpmFilterDeleteById0(e, id);
      return "id=" + id + " -> 0x" + r.ToString("X");
    } finally { FwpmEngineClose0(e); }
  }
}
'@
Add-Type -TypeDefinition $cs -ErrorAction SilentlyContinue

$wfp = Join-Path $env:TEMP ("wfp_ks_{0}.xml" -f [Environment]::TickCount)
netsh wfp show filters file=$wfp | Out-Null
$xml = Get-Content -LiteralPath $wfp -Raw
$ids = [regex]::Matches($xml, '(?s)<name>Block Internet</name>.*?<filterId>(\d+)</filterId>') |
  ForEach-Object { [UInt64]$_.Groups[1].Value } | Select-Object -Unique

Write-Host "Block Internet filters: $($ids.Count)"
foreach ($id in $ids) {
  Write-Host ("  " + [WfpKillSwitchOff]::Del($id))
}

$tunnelLike = 'tun|wt0|ZeroTier|vEthernet|Loopback|Tailscale|Wintun|Amnezia'
$def = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 -EA SilentlyContinue |
  Where-Object { $_.NextHop -ne '0.0.0.0' -and $_.InterfaceAlias -notmatch $tunnelLike } |
  Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1
if (-not $def) { throw 'LAN default route not found' }
Write-Host "LAN ifIndex=$($def.InterfaceIndex) gw=$($def.NextHop)"

foreach ($c in $Cidrs) {
  if (Get-NetRoute -DestinationPrefix $c -InterfaceIndex $def.InterfaceIndex -EA SilentlyContinue) {
    Write-Host "  route $c OK"; continue
  }
  New-NetRoute -DestinationPrefix $c -InterfaceIndex $def.InterfaceIndex -NextHop $def.NextHop -RouteMetric 1 | Out-Null
  Write-Host "  route $c added"
}

Write-Host 'VK kill-switch bypass applied. Test https://vk.com/'
