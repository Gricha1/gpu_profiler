#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install Task Scheduler job GPUProfiler-MeshRouteWatcher (At logon, hidden, restart).
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$Watcher = Join-Path $Root 'scripts\amnezia\mesh_route_watcher.ps1'
$TaskName = 'GPUProfiler-MeshRouteWatcher'

if (-not (Test-Path -LiteralPath $Watcher)) {
  throw "Watcher script missing: $Watcher"
}

# Unregister previous
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Watcher`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Description 'GPU Profiler: remove Amnezia Wi-Fi hijack routes for NetBird/ZeroTier after Connect' | Out-Null

# Start now
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

$t = Get-ScheduledTask -TaskName $TaskName
$i = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Task=$TaskName State=$($t.State) LastTaskResult=$($i.LastTaskResult)"
Write-Host "Watcher=$Watcher"
Write-Host "Status will appear at: $(Join-Path $Root 'runtime\mesh_route_watcher_status.json')"
