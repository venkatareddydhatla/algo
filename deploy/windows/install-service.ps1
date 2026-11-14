<#
.SYNOPSIS
  Install Options Portal as a Windows service (manual/demand start by default).

.DESCRIPTION
  Uses NSSM (Non-Sucking Service Manager) to wrap:
    docker compose -f deploy/docker-compose.yml up
  WorkingDirectory = repository root. Creates .env from .env.example if missing
  (never overwrites an existing .env).

  Default start type is demand (manual). Pass -AutoStart for automatic start
  after boot (still requires Docker Desktop to be available).

  NSSM is NOT bundled. Download once from the official site, or pass -DownloadNssm
  to fetch the release zip into deploy/windows/tools/nssm/ (see docs).

.PARAMETER NssmPath
  Full path to nssm.exe (win64). Optional if already under deploy/windows/tools/nssm/win64/nssm.exe or on PATH.

.PARAMETER DownloadNssm
  Download the official NSSM 2.24 release zip into deploy/windows/tools/ and extract win64.

.PARAMETER AutoStart
  Set service start type to Automatic. Default is Manual (demand).

.PARAMETER ServiceName
  Windows service name (default OptionsPortal).

.EXAMPLE
  # From an elevated PowerShell, after Docker Desktop is running:
  .\deploy\windows\install-service.ps1 -DownloadNssm
  Start-Service OptionsPortal
#>
[CmdletBinding()]
param(
    [string]$NssmPath,
    [switch]$DownloadNssm,
    [switch]$AutoStart,
    [string]$ServiceName = 'OptionsPortal'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')
$script:ServiceName = $ServiceName

Assert-Administrator -Action 'service install'
$repoRoot = Get-RepoRoot
Ensure-DotEnv -RepoRoot $repoRoot | Out-Null
Assert-DockerAvailable

$composeFile = Get-ComposeFile -RepoRoot $repoRoot
if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "Compose file not found: $composeFile"
}

function Install-NssmFromOfficialZip {
    param([string]$RepoRoot)
    # Official NSSM download page: https://nssm.cc/download
    # Pin 2.24 release zip (stable). License: NSSM public domain / free use — review https://nssm.cc/
    $url = 'https://nssm.cc/release/nssm-2.24.zip'
    $tools = Join-Path $RepoRoot 'deploy\windows\tools'
    $zip = Join-Path $tools 'nssm-2.24.zip'
    $extract = Join-Path $tools 'nssm-2.24-extract'
    $dest = Join-Path $tools 'nssm'

    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    Write-Host "Downloading NSSM 2.24 from $url ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

    if (Test-Path -LiteralPath $extract) {
        Remove-Item -LiteralPath $extract -Recurse -Force
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force

    $win64 = Get-ChildItem -Path $extract -Recurse -Filter 'nssm.exe' |
        Where-Object { $_.DirectoryName -match '\\win64$' } |
        Select-Object -First 1
    if (-not $win64) {
        throw "nssm.exe (win64) not found inside downloaded zip. Extract manually from https://nssm.cc/download"
    }

    if (Test-Path -LiteralPath $dest) {
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -LiteralPath $win64.DirectoryName -Destination (Join-Path $dest 'win64') -Recurse -Force
    Write-Host "NSSM installed locally at $(Join-Path $dest 'win64\nssm.exe')"
    return (Join-Path $dest 'win64\nssm.exe')
}

$nssm = Find-Nssm -NssmPath $NssmPath -RepoRoot $repoRoot
if (-not $nssm) {
    if ($DownloadNssm) {
        $nssm = Install-NssmFromOfficialZip -RepoRoot $repoRoot
    } else {
        throw @"
NSSM (nssm.exe) not found.
Options:
  1) Download once from https://nssm.cc/download (2.24 recommended), extract win64\nssm.exe to:
       $($repoRoot)\deploy\windows\tools\nssm\win64\nssm.exe
  2) Re-run with -DownloadNssm to fetch the official 2.24 zip into deploy\windows\tools\
  3) Pass -NssmPath 'C:\path\to\nssm.exe'
"@
    }
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service '$ServiceName' already exists (Status=$($existing.Status)). Reconfigure with NSSM..."
} else {
    Write-Host "Installing service '$ServiceName' via NSSM..."
    $dockerExe = Get-DockerExePath
    & $nssm install $ServiceName $dockerExe
    if ($LASTEXITCODE -ne 0) {
        throw "nssm install failed with exit code $LASTEXITCODE"
    }
}

$dockerExe = Get-DockerExePath
$logDir = Join-Path $repoRoot 'deploy\windows\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Application: docker.exe  Args: compose -f <abs compose> up
# Working directory = repo root so relative mounts in compose resolve.
& $nssm set $ServiceName Application $dockerExe | Out-Null
& $nssm set $ServiceName AppDirectory $repoRoot | Out-Null
& $nssm set $ServiceName AppParameters "compose -f `"$composeFile`" up" | Out-Null
& $nssm set $ServiceName DisplayName 'Options Portal (Docker Compose)' | Out-Null
& $nssm set $ServiceName Description 'Runs options-portal docker compose stack (dry_run default). Start manually after Docker Desktop is ready.' | Out-Null
& $nssm set $ServiceName Start $(if ($AutoStart) { 'SERVICE_AUTO_START' } else { 'SERVICE_DEMAND_START' }) | Out-Null
& $nssm set $ServiceName AppStdout (Join-Path $logDir 'service-stdout.log') | Out-Null
& $nssm set $ServiceName AppStderr (Join-Path $logDir 'service-stderr.log') | Out-Null
& $nssm set $ServiceName AppRotateFiles 1 | Out-Null
& $nssm set $ServiceName AppExit Default Restart | Out-Null
& $nssm set $ServiceName AppRestartDelay 5000 | Out-Null

$startType = if ($AutoStart) { 'Automatic' } else { 'Manual (demand)' }
Write-Host ""
Write-Host "Installed / configured service: $ServiceName"
Write-Host "  Start type:     $startType"
Write-Host "  Working dir:    $repoRoot"
Write-Host "  Compose file:   $composeFile"
Write-Host "  Command:        $dockerExe compose -f `"$composeFile`" up"
Write-Host "  Logs:           $logDir"
Write-Host ""
Write-Host "Next (Docker Desktop must already be running):"
Write-Host "  Start-Service $ServiceName"
Write-Host "  # or:  .\deploy\windows\start-portal.ps1"
Write-Host ""
Write-Host "URLs (after containers are up):"
Write-Host "  UI:  http://127.0.0.1:5173"
Write-Host "  API: http://127.0.0.1:8000/docs"
Write-Host ""
Write-Host "Trading mode remains dry_run by default. No live orders in v1."
