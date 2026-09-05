#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$StartPs1 = Join-Path $Root 'scripts\start.ps1'
$Desktop = [Environment]::GetFolderPath('Desktop')
$LnkPath = Join-Path $Desktop 'GPU Profiler.lnk'
$StopLnk = Join-Path $Desktop 'GPU Profiler Stop.lnk'
$StopPs1 = Join-Path $Root 'scripts\stop.ps1'

$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($LnkPath)
$s.TargetPath = 'powershell.exe'
$s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$StartPs1`""
$s.WorkingDirectory = $Root
$s.WindowStyle = 7
$s.Description = 'Start GPU Profiler UI (http://127.0.0.1:8765/)'
$s.Save()

$s2 = $w.CreateShortcut($StopLnk)
$s2.TargetPath = 'powershell.exe'
$s2.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StopPs1`""
$s2.WorkingDirectory = $Root
$s2.Description = 'Stop GPU Profiler backend only'
$s2.Save()

Write-Host "Created: $LnkPath"
Write-Host "Created: $StopLnk"
