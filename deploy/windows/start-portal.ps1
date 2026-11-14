<#
.SYNOPSIS
  Start the Options Portal stack (Windows service if installed, else Compose).

.DESCRIPTION
  1) Ensures .env exists (copy from .env.example; never overwrite).
  2) Checks Docker Desktop is available.
  3) If the OptionsPortal service exists: Start-Service.
     Else: docker compose -f deploy/docker-compose.yml up -d --build

.PARAMETER ServiceName
  Windows service name (default OptionsPortal).

.PARAMETER Foreground
  When falling back to Compose (no service), run foreground `up` instead of `up -d`.

.PARAMETER NoBuild
  When falling back to Compose, omit --build.

.EXAMPLE
  .\deploy\windows\start-portal.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = 'OptionsPortal',
    [switch]$Foreground,
    [switch]$NoBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')
$script:ServiceName = $ServiceName

$repoRoot = Get-RepoRoot
Ensure-DotEnv -RepoRoot $repoRoot | Out-Null
Assert-DockerAvailable

$state = Get-ServiceState -Name $ServiceName
if ($null -ne $state) {
    if ($state -eq 'Running') {
        Write-Host "Service '$ServiceName' is already Running."
    } else {
        Write-Host "Starting Windows service '$ServiceName' (was $state)..."
        try {
            Start-Service -Name $ServiceName
        } catch {
            throw "Failed to start service '$ServiceName'. Run PowerShell as Administrator, or: Start-Service $ServiceName. Detail: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
        $after = Get-ServiceState -Name $ServiceName
        Write-Host "Service status: $after"
    }
} else {
    Write-Host "Service '$ServiceName' not installed. Starting Compose directly..."
    $composeArgs = @('up')
    if (-not $Foreground) { $composeArgs += '-d' }
    if (-not $NoBuild) { $composeArgs += '--build' }
    Invoke-Compose -RepoRoot $repoRoot -ComposeArgs $composeArgs
}

Write-Host ""
Write-Host "When healthy:"
Write-Host "  UI:  http://127.0.0.1:5173"
Write-Host "  API: http://127.0.0.1:8000/docs"
Write-Host "Trading mode: dry_run (default). Live placement blocked in v1."
