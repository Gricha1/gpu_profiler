#Requires -Version 5.1
<#
.SYNOPSIS
  Build launchers and place exactly two Desktop shortcuts:
    GPU Profiler.lnk  -> launchers\GPU Profiler.exe
    Mesh Watcher.lnk  -> launchers\Mesh Watcher.exe
  Removes old GPU Profiler Stop / raw powershell shortcuts.
#>
$ErrorActionPreference = 'Stop'
$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = Split-Path -Parent $Here
$Build = Join-Path $Here 'build.ps1'
& $Build

$gpuExe = Join-Path $Here 'GPU Profiler.exe'
$meshExe = Join-Path $Here 'Mesh Watcher.exe'
if (-not (Test-Path $gpuExe)) { throw "Missing $gpuExe" }
if (-not (Test-Path $meshExe)) { throw "Missing $meshExe" }

$Desktop = [Environment]::GetFolderPath('Desktop')
$w = New-Object -ComObject WScript.Shell

# Remove old clutter
@(
  'GPU Profiler Stop.lnk',
  'GPU Profiler.bat',
  'Mesh Watcher.bat',
  'Start Mesh Watcher.lnk',
  'Stop Mesh Watcher.lnk'
) | ForEach-Object {
  $p = Join-Path $Desktop $_
  if (Test-Path $p) { Remove-Item -LiteralPath $p -Force; Write-Host "Removed: $p" }
}

# Also remove old powershell-based GPU Profiler.lnk so we replace it cleanly
$oldGpu = Join-Path $Desktop 'GPU Profiler.lnk'
if (Test-Path $oldGpu) { Remove-Item -LiteralPath $oldGpu -Force }

$gpuLnk = Join-Path $Desktop 'GPU Profiler.lnk'
$s = $w.CreateShortcut($gpuLnk)
$s.TargetPath = $gpuExe
$s.WorkingDirectory = $Here
$s.Description = 'Start/open GPU Profiler UI (http://127.0.0.1:8765/)'
$s.WindowStyle = 1
$s.Save()

$meshLnk = Join-Path $Desktop 'Mesh Watcher.lnk'
$s2 = $w.CreateShortcut($meshLnk)
$s2.TargetPath = $meshExe
$s2.WorkingDirectory = $Here
$s2.Description = 'Start/check GPUProfiler-MeshRouteWatcher scheduled task'
$s2.WindowStyle = 1
$s2.Save()

Write-Host "Desktop: $gpuLnk"
Write-Host "Desktop: $meshLnk"
Write-Host "Targets:"
Write-Host "  $gpuExe"
Write-Host "  $meshExe"
