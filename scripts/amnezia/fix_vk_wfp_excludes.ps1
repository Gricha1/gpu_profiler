#Requires -Version 5.1
<#
.SYNOPSIS
  DEPRECATED name — use fix_direct_site_routes.ps1

  This wrapper NO LONGER deletes Amnezia kill-switch / Block Internet.
  It only refreshes VK/Yandex routes onto the current LAN gateway.
#>
[CmdletBinding()]
param([switch]$WhatIf, [switch]$Quiet)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $here 'fix_direct_site_routes.ps1') -WhatIf:$WhatIf -Quiet:$Quiet
