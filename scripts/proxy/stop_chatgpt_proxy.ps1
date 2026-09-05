#Requires -Version 5.1
<#
.SYNOPSIS
  Stop ONLY the ChatGPT selective proxy started by GPU Profiler.
  Never mass-kills sing-box.exe or unrelated ssh.exe.
  Does not touch Amnezia / NetBird / ZeroTier.
#>
$ErrorActionPreference = 'Continue'
$VpnDir = Join-Path $env:USERPROFILE '.ssh\timeweb-vpn'
$PidFile = Join-Path $VpnDir 'sing-box-chatgpt.pid'
$LegacyPidFile = Join-Path $VpnDir 'sing-box.pid'
$Lib = Join-Path $VpnDir 'chatgpt-proxy-lib.ps1'
$PacPidFile = Join-Path $VpnDir 'pac-server.pid'

function Stop-TrackedPid([string]$Path, [string]$MustMatch) {
  if (-not (Test-Path $Path)) { return }
  $raw = (Get-Content $Path -Raw -ErrorAction SilentlyContinue).Trim()
  if ($raw -notmatch '^\d+$') { Remove-Item $Path -Force -ErrorAction SilentlyContinue; return }
  $id = [int]$raw
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
  if ($proc) {
    if ($MustMatch -and ($proc.CommandLine -notmatch $MustMatch)) {
      Write-Host "skip pid=$id (cmdline does not match $MustMatch)"
    } else {
      Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
      Write-Host "stopped pid=$id"
    }
  }
  Remove-Item $Path -Force -ErrorAction SilentlyContinue
}

# Prefer PID files
Stop-TrackedPid $PidFile 'config-chatgpt\.json|sing-box'
Stop-TrackedPid $LegacyPidFile 'config-chatgpt\.json'

# Fallback: only chatgpt-config sing-box still listening
$chatgpt = Get-CimInstance Win32_Process -Filter "Name='sing-box.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'config-chatgpt\.json' }
foreach ($p in $chatgpt) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  Write-Host "stopped chatgpt sing-box pid=$($p.ProcessId)"
}

# Optional PAC fallback server (only if we tracked it)
if (Test-Path $PacPidFile) {
  Stop-TrackedPid $PacPidFile 'chatgpt-pac-server|18080'
}

if (Test-Path $Lib) {
  . $Lib
  Set-ChatGptYandexProxyOff
}

Remove-Item -LiteralPath (Join-Path $VpnDir 'chatgpt-proxy.on') -Force -ErrorAction SilentlyContinue
Write-Host 'ChatGPT PROXY OFF'
