#Requires -Version 5.1
<#
.SYNOPSIS
  Build GPU Profiler.exe and Mesh Watcher.exe (csc / .NET Framework, no NuGet).
#>
$ErrorActionPreference = 'Stop'
$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = Split-Path -Parent $Here
$Src = Join-Path $Here 'src'
$Out = $Here
$Csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $Csc)) {
  $Csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path $Csc)) { throw 'csc.exe not found (.NET Framework 4.x required)' }

$refs = @(
  '/reference:System.dll',
  '/reference:System.Core.dll',
  '/reference:System.Windows.Forms.dll',
  '/reference:System.Drawing.dll'
)

$gpuOut = Join-Path $Out 'GPU Profiler.exe'
$meshOut = Join-Path $Out 'Mesh Watcher.exe'

& $Csc /nologo /optimize+ /target:winexe /platform:anycpu `
  /out:$gpuOut @refs `
  (Join-Path $Src 'GpuProfilerLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw "csc GPU Profiler failed: $LASTEXITCODE" }

& $Csc /nologo /optimize+ /target:winexe /platform:anycpu `
  /out:$meshOut @refs `
  (Join-Path $Src 'MeshWatcherLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw "csc Mesh Watcher failed: $LASTEXITCODE" }

Write-Host "Built: $gpuOut"
Write-Host "Built: $meshOut"
Get-Item $gpuOut, $meshOut | Format-Table Name, Length, LastWriteTime -AutoSize
