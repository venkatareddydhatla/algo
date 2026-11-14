<#
.SYNOPSIS
  Fallback: register a Manual Scheduled Task that starts the Compose stack.

.DESCRIPTION
  Use when NSSM / Windows service install is undesirable. Creates task
  \OptionsPortal\StartStack that runs docker compose up -d --build with
  WorkingDirectory = repo root. Does not auto-start at logon unless -AtLogon.

.PARAMETER AtLogon
  Also trigger at interactive logon (still requires Docker Desktop ready).

.EXAMPLE
  .\deploy\windows\install-scheduled-task.ps1
  Start-ScheduledTask -TaskPath '\OptionsPortal\' -TaskName 'StartStack'
#>
[CmdletBinding()]
param(
    [switch]$AtLogon
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

Assert-Administrator -Action 'scheduled task install'
$repoRoot = Get-RepoRoot
Ensure-DotEnv -RepoRoot $repoRoot | Out-Null
Assert-DockerAvailable

$composeFile = Get-ComposeFile -RepoRoot $repoRoot
$dockerExe = Get-DockerExePath
$taskPath = '\OptionsPortal\'
$taskName = 'StartStack'

$arg = "compose -f `"$composeFile`" up -d --build"
$action = New-ScheduledTaskAction -Execute $dockerExe -Argument $arg -WorkingDirectory $repoRoot
$triggers = @()
# Manual-only by default: a one-time trigger in the past keeps the task registered without running.
$triggers += New-ScheduledTaskTrigger -Once -At (Get-Date).AddYears(-1)
if ($AtLogon) {
    $triggers += New-ScheduledTaskTrigger -AtLogOn
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered scheduled task ${taskPath}${taskName}"
Write-Host "  WorkingDirectory: $repoRoot"
Write-Host "  Command: $dockerExe $arg"
Write-Host ""
Write-Host "Start on demand:"
Write-Host "  Start-ScheduledTask -TaskPath '\OptionsPortal\' -TaskName 'StartStack'"
Write-Host "  # or: .\deploy\windows\start-portal.ps1   (Compose fallback if no service)"
Write-Host ""
Write-Host "Prefer the NSSM Windows service (install-service.ps1) when possible."
