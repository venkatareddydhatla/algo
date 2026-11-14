<#
.SYNOPSIS
  Stop native API / web / worker processes started by start-native.ps1.

.DESCRIPTION
  Reads PIDs from deploy\windows\native\logs\*.pid and stops those processes.
  Does not touch Docker Compose or the OptionsPortal NSSM Docker service.

.EXAMPLE
  .\deploy\windows\native\stop-native.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_native-common.ps1')

$repoRoot = Get-NativeRepoRoot
$logsDir = Get-NativeLogsDir -RepoRoot $repoRoot

Write-Host "Stopping native Options Portal processes..."
Stop-PidFileProcess -PidFile (Join-Path $logsDir 'web.pid') -Label 'portal-web'
Stop-PidFileProcess -PidFile (Join-Path $logsDir 'worker.pid') -Label 'worker'
Stop-PidFileProcess -PidFile (Join-Path $logsDir 'api.pid') -Label 'API'

# Best-effort: stop orphaned child node/uvicorn if PID file was stale but ports held
# (optional — do not kill unrelated processes by name globally)

Write-Host "Native stop complete."
Write-Host "Docker/NSSM stack (if any) is unchanged — use .\deploy\windows\stop-portal.ps1 for that."
