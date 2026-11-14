<#
.SYNOPSIS
  Stop the Options Portal stack (Windows service and/or Compose down).

.DESCRIPTION
  If the OptionsPortal service exists and is running, stops it.
  Always runs `docker compose down --remove-orphans` when Docker is available
  so containers are not left running after the service process exits.

.PARAMETER ServiceName
  Windows service name (default OptionsPortal).

.PARAMETER RemoveVolumes
  Also pass -v to compose down (destroys the Postgres volume).

.EXAMPLE
  .\deploy\windows\stop-portal.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = 'OptionsPortal',
    [switch]$RemoveVolumes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')
$script:ServiceName = $ServiceName

$repoRoot = Get-RepoRoot
$state = Get-ServiceState -Name $ServiceName

if ($null -ne $state) {
    if ($state -eq 'Stopped') {
        Write-Host "Service '$ServiceName' is already Stopped."
    } else {
        Write-Host "Stopping Windows service '$ServiceName' (was $state)..."
        try {
            Stop-Service -Name $ServiceName -Force
        } catch {
            throw "Failed to stop service '$ServiceName'. Run PowerShell as Administrator, or: Stop-Service $ServiceName. Detail: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
        Write-Host "Service status: $(Get-ServiceState -Name $ServiceName)"
    }
} else {
    Write-Host "Service '$ServiceName' not installed; will compose down only."
}

try {
    Assert-DockerAvailable
    $downArgs = @('down', '--remove-orphans')
    if ($RemoveVolumes) { $downArgs += '-v' }
    Write-Host "Running docker compose $($downArgs -join ' ') ..."
    Invoke-Compose -RepoRoot $repoRoot -ComposeArgs $downArgs
} catch {
    Write-Warning "Compose down skipped or failed: $($_.Exception.Message)"
}

Write-Host "Portal stack stop requested."
