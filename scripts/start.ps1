#Requires -Version 5.1
<#
.SYNOPSIS
  Start GPU Profiler on 127.0.0.1:8765 if not already running, then open the UI.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HostAddr = '127.0.0.1'
$Port = 8765
$Url = "http://${HostAddr}:${Port}/"
$PidFile = Join-Path $Root 'logs\gpu_profiler.pid'
$LogDir = Join-Path $Root 'logs'
$OutLog = Join-Path $LogDir 'uvicorn.out.log'
$ErrLog = Join-Path $LogDir 'uvicorn.err.log'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-LocalPort([int]$PortNum) {
  try {
    $c = [System.Net.Sockets.TcpClient]::new()
    $iar = $c.BeginConnect($HostAddr, $PortNum, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(300)
    if ($ok -and $c.Connected) { $c.Close(); return $true }
    try { $c.Close() } catch {}
  } catch {}
  return $false
}

function Wait-HttpReady([int]$TimeoutSec = 30) {
  $deadline = [Environment]::TickCount + ($TimeoutSec * 1000)
  while ([Environment]::TickCount -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { return $true }
    } catch {}
    Start-Sleep -Milliseconds 400
  }
  return $false
}

if (Test-LocalPort $Port) {
  Write-Host "GPU Profiler already running on $Url"
  Start-Process $Url
  exit 0
}

# Prefer pythonw-less visible minimized console via python -m uvicorn
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) { throw 'python not found on PATH' }
$python = $pyCmd.Source

$arg = "-m uvicorn app:app --host $HostAddr --port $Port"
$proc = Start-Process -FilePath $python -ArgumentList $arg `
  -WorkingDirectory $Root `
  -WindowStyle Minimized `
  -PassThru `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog

Set-Content -LiteralPath $PidFile -Value $proc.Id

if (-not (Wait-HttpReady 35)) {
  Write-Host "ERROR: GPU Profiler did not become ready. See $ErrLog"
  exit 1
}

Write-Host "GPU Profiler ready: $Url (pid=$($proc.Id))"
Start-Process $Url
