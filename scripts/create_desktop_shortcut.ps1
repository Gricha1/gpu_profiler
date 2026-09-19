#Requires -Version 5.1
# Compatibility wrapper — prefer launchers\install_desktop_launchers.ps1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
& (Join-Path $Root 'launchers\install_desktop_launchers.ps1')
