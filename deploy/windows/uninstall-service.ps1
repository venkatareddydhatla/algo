<#
.SYNOPSIS
  Remove the Options Portal Windows service installed by install-service.ps1.

.DESCRIPTION
  Stops the service (if running), brings the Compose stack down, then removes
  the NSSM-managed Windows service. Does not delete .env, images, or volumes
  unless -RemoveVolumes is passed (compose down -v).

.PARAMETER ServiceName
  Windows service name (default OptionsPortal).

.PARAMETER NssmPath
  Optional path to nssm.exe.

.PARAMETER RemoveVolumes
  Also run `docker compose down -v` (destroys the Postgres volume).

.EXAMPLE
  .\deploy\windows\uninstall-service.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = 'OptionsPortal',
    [string]$NssmPath,
    [switch]$RemoveVolumes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')
$script:ServiceName = $ServiceName

Assert-Administrator -Action 'service uninstall'
$repoRoot = Get-RepoRoot

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne 'Stopped') {
        Write-Host "Stopping service $ServiceName ..."
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
} else {
    Write-Host "Service '$ServiceName' is not installed."
}

# Always attempt a clean compose down so containers are not left orphaned.
try {
    Assert-DockerAvailable
    $downArgs = @('down', '--remove-orphans')
    if ($RemoveVolumes) { $downArgs += '-v' }
    Write-Host "Running docker compose $($downArgs -join ' ') ..."
    Invoke-Compose -RepoRoot $repoRoot -ComposeArgs $downArgs
} catch {
    Write-Warning "Compose down skipped or failed: $($_.Exception.Message)"
}

$nssm = Find-Nssm -NssmPath $NssmPath -RepoRoot $repoRoot
if ($svc) {
    if ($nssm) {
        Write-Host "Removing service via NSSM..."
        & $nssm stop $ServiceName confirm 2>$null
        & $nssm remove $ServiceName confirm
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "nssm remove returned $LASTEXITCODE; trying sc.exe delete"
            & sc.exe delete $ServiceName | Out-Null
        }
    } else {
        Write-Host "NSSM not found; removing with sc.exe delete..."
        & sc.exe delete $ServiceName | Out-Null
    }
    Write-Host "Service '$ServiceName' removed (or removal requested)."
} else {
    Write-Host "Nothing to uninstall for service name '$ServiceName'."
}

Write-Host "Done. .env was left in place (secrets preserved)."
