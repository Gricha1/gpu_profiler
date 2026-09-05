#Requires -Version 5.1
<#
.SYNOPSIS
  Start ChatGPT selective PAC proxy (PID-tracked).
  Uses ~/.ssh/timeweb-vpn sing-box + config-chatgpt.json.
  Does NOT kill unrelated sing-box / ssh / Amnezia / NetBird / ZeroTier.
#>
$ErrorActionPreference = 'Stop'
$VpnDir = Join-Path $env:USERPROFILE '.ssh\timeweb-vpn'
$Exe = Join-Path $VpnDir 'sing-box.exe'
$Cfg = Join-Path $VpnDir 'config-chatgpt.json'
$PidFile = Join-Path $VpnDir 'sing-box-chatgpt.pid'
$LegacyPidFile = Join-Path $VpnDir 'sing-box.pid'
$ErrLog = Join-Path $VpnDir 'sing-box-chatgpt.err'
$Lib = Join-Path $VpnDir 'chatgpt-proxy-lib.ps1'
$PacServer = Join-Path $VpnDir 'chatgpt-pac-server.ps1'

function Test-LocalPort([int]$Port) {
  try {
    $c = [System.Net.Sockets.TcpClient]::new()
    $iar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(250)
    if ($ok -and $c.Connected) { $c.Close(); return $true }
    try { $c.Close() } catch {}
  } catch {}
  return $false
}

function Wait-LocalPort([int]$Port, [int]$TimeoutMs = 8000) {
  $deadline = [Environment]::TickCount + $TimeoutMs
  while ([Environment]::TickCount -lt $deadline) {
    if (Test-LocalPort $Port) { return $true }
    Start-Sleep -Milliseconds 120
  }
  return $false
}

function Get-ChatGptSingBoxPid {
  $procs = Get-CimInstance Win32_Process -Filter "Name='sing-box.exe'" -ErrorAction SilentlyContinue
  foreach ($p in $procs) {
    if ($p.CommandLine -match 'config-chatgpt\.json') { return [int]$p.ProcessId }
  }
  return $null
}

function Stop-OurChatGptProxyOnly {
  $candidates = @()
  foreach ($f in @($PidFile, $LegacyPidFile)) {
    if (Test-Path $f) {
      $raw = (Get-Content $f -Raw -ErrorAction SilentlyContinue).Trim()
      if ($raw -match '^\d+$') { $candidates += [int]$raw }
    }
  }
  $live = Get-ChatGptSingBoxPid
  if ($live) { $candidates += $live }

  foreach ($id in ($candidates | Select-Object -Unique)) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    if ($proc.Name -ne 'sing-box.exe') { continue }
    if ($proc.CommandLine -notmatch 'config-chatgpt\.json') { continue }
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
  }

  # If something else holds 10808 for chatgpt config, stop only that listener PID when cmdline matches.
  if (Test-LocalPort 10808) {
    $owner = Get-ChatGptSingBoxPid
    if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue }
  }
}

if (-not (Test-Path $Exe)) { throw "sing-box.exe missing: $Exe" }
if (-not (Test-Path $Cfg)) { throw "config missing: $Cfg" }
if (-not (Test-Path $Lib)) { throw "chatgpt-proxy-lib.ps1 missing: $Lib" }

. $Lib

$existing = Get-ChatGptSingBoxPid
if ($existing -and (Test-LocalPort 10808)) {
  Set-Content -LiteralPath $PidFile -Value $existing
  Set-Content -LiteralPath $LegacyPidFile -Value $existing
  if (-not (Test-LocalPort 18080) -and (Test-Path $PacServer)) {
    Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$PacServer -WindowStyle Hidden | Out-Null
  }
  Set-ChatGptYandexProxyOn
  Write-Host "ChatGPT PROXY ON (already running pid=$existing)"
  exit 0
}

Stop-OurChatGptProxyOnly
Start-Sleep -Milliseconds 250

$sb = Start-Process -FilePath $Exe -ArgumentList @('run', '-c', $Cfg) -WindowStyle Hidden -PassThru `
  -RedirectStandardError $ErrLog
Set-Content -LiteralPath $PidFile -Value $sb.Id
Set-Content -LiteralPath $LegacyPidFile -Value $sb.Id

if (-not (Wait-LocalPort 10808 8000)) {
  $err = if (Test-Path $ErrLog) { Get-Content $ErrLog -Raw } else { '' }
  throw "sing-box :10808 failed. $err"
}

if (-not (Test-LocalPort 18080) -and (Test-Path $PacServer)) {
  Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$PacServer -WindowStyle Hidden | Out-Null
  [void](Wait-LocalPort 18080 2500)
}

Set-ChatGptYandexProxyOn
Write-Host "ChatGPT PROXY ON (pid=$($sb.Id))"
Write-Host 'PAC: http://127.0.0.1:8765/chatgpt.pac (fallback :18080)'
