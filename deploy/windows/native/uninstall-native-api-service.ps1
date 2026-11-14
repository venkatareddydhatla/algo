<#
.SYNOPSIS
  Remove the optional native API Windows service (OptionsPortalApiNative).

.EXAMPLE
  .\deploy\windows\native\uninstall-native-api-service.ps1
#>
[CmdletBinding()]
param(
    [string]$NssmPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_native-common.ps1')

if (-not (Test-IsAdministrator)) {
    throw "Administrator privileges required."
}

$repoRoot = Get-NativeRepoRoot
$nssm = Find-Nssm -NssmPath $NssmPath -RepoRoot $repoRoot
$svc = $script:NativeServiceName
$existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Service '$svc' is not installed."
    return
}

if ($existing.Status -eq 'Running') {
    Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

if ($nssm) {
    & $nssm remove $svc confirm
} else {
    # Fallback
    sc.exe delete $svc | Out-Null
}

Write-Host "Removed service '$svc'."
