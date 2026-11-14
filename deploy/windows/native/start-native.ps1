<#
.SYNOPSIS
  Start API + Vite web UI natively on Windows (no Docker).

.DESCRIPTION
  Loads .env (creates SQLite-friendly .env if missing), starts uvicorn and
  Vite in background windows, writes PIDs under deploy\windows\native\logs\.

.PARAMETER WithWorker
  Also start the Python worker pipeline tick loop.

.PARAMETER ForegroundApi
  Run uvicorn in the current console instead of a background process
  (web still starts in background unless -NoWeb).

.PARAMETER NoWeb
  Start API only (skip Vite).

.EXAMPLE
  .\deploy\windows\native\start-native.ps1
.EXAMPLE
  .\deploy\windows\native\start-native.ps1 -WithWorker
#>
[CmdletBinding()]
param(
    [switch]$WithWorker,
    [switch]$ForegroundApi,
    [switch]$NoWeb
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_native-common.ps1')

$repoRoot = Get-NativeRepoRoot
Set-Location $repoRoot

$envPath = Ensure-NativeDotEnv -RepoRoot $repoRoot
Import-DotEnvFile -EnvPath $envPath

# Force dry-run safety defaults if somehow unset
if (-not $env:TRADING_MODE) { $env:TRADING_MODE = 'dry_run' }
if (-not $env:PERSISTENCE_PROFILE) { $env:PERSISTENCE_PROFILE = 'local' }
if (-not $env:CONFIG_PATH) { $env:CONFIG_PATH = './config/local.yaml' }

$dataDir = Join-Path $repoRoot 'data'
if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}

# Resolve relative SQLite URL against repo root (API cwd is apps\api)
$dbUrl = $env:DATABASE_URL
if ($dbUrl -and $dbUrl -match '^sqlite:///(\./)?data/') {
    $sqliteFile = Join-Path $dataDir 'options_portal.local.sqlite'
    $unixPath = ($sqliteFile -replace '\\', '/')
    $env:DATABASE_URL = "sqlite:///$unixPath"
}

# CONFIG_PATH relative to repo root - set absolute so apps\* cwd works
$cfg = $env:CONFIG_PATH
if ($cfg -and -not [System.IO.Path]::IsPathRooted($cfg)) {
    $env:CONFIG_PATH = (Join-Path $repoRoot ($cfg -replace '^\./', '' -replace '/', '\'))
}

$logsDir = Get-NativeLogsDir -RepoRoot $repoRoot
$apiPidFile = Join-Path $logsDir 'api.pid'
$webPidFile = Join-Path $logsDir 'web.pid'
$workerPidFile = Join-Path $logsDir 'worker.pid'
$apiOut = Join-Path $logsDir 'api-stdout.log'
$apiErr = Join-Path $logsDir 'api-stderr.log'
$webOut = Join-Path $logsDir 'web-stdout.log'
$webErr = Join-Path $logsDir 'web-stderr.log'
$workerOut = Join-Path $logsDir 'worker-stdout.log'
$workerErr = Join-Path $logsDir 'worker-stderr.log'

$uvicorn = Get-VenvUvicorn -RepoRoot $repoRoot
$python = Get-VenvPython -RepoRoot $repoRoot
$hostBind = if ($env:BIND_HOST) { $env:BIND_HOST } else { '127.0.0.1' }
$port = if ($env:API_PORT) { $env:API_PORT } else { '8000' }

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath,
        [Parameter(Mandatory = $true)][string]$PidPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $existing = Read-PidFile -Path $PidPath
    if ($null -ne $existing) {
        $alive = Get-Process -Id $existing -ErrorAction SilentlyContinue
        if ($alive) {
            Write-Host ('{0} already running as PID {1} - skip.' -f $Label, $existing)
            return $existing
        }
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    }

    # Inherit current process env (includes Import-DotEnvFile values)
    $proc = Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru `
        -WindowStyle Hidden

    Set-Content -LiteralPath $PidPath -Value $proc.Id -Encoding ASCII
    Write-Host ('Started {0} PID {1} (logs: {2} / {3})' -f $Label, $proc.Id, $StdoutPath, $StderrPath)
    return $proc.Id
}

Write-Host 'Starting native Options Portal (no Docker)...'
Write-Host "  PERSISTENCE_PROFILE=$($env:PERSISTENCE_PROFILE)"
Write-Host "  TRADING_MODE=$($env:TRADING_MODE)"

$apiArgs = @(
    'app.main:app',
    '--host', $hostBind,
    '--port', $port
)

if ($ForegroundApi) {
    if (-not $NoWeb) {
        Assert-Command -Name 'npm' -Hint 'Install Node 20+.' | Out-Null
        Start-LoggedProcess `
            -FilePath "$env:ComSpec" `
            -ArgumentList @('/c', 'npm', 'run', 'dev') `
            -WorkingDirectory (Join-Path $repoRoot 'apps\portal-web') `
            -StdoutPath $webOut `
            -StderrPath $webErr `
            -PidPath $webPidFile `
            -Label 'portal-web' | Out-Null
    }
    if ($WithWorker) {
        Start-LoggedProcess `
            -FilePath $python `
            -ArgumentList @('-m', 'app.main') `
            -WorkingDirectory (Join-Path $repoRoot 'apps\worker') `
            -StdoutPath $workerOut `
            -StderrPath $workerErr `
            -PidPath $workerPidFile `
            -Label 'worker' | Out-Null
    }
    Write-Host 'Running API in foreground (Ctrl+C to stop API only; use stop-native.ps1 for web/worker)...'
    Write-Host '  UI:  http://127.0.0.1:5173'
    Write-Host "  API: http://127.0.0.1:$port/docs"
    Set-Location (Join-Path $repoRoot 'apps\api')
    & $uvicorn @apiArgs
    exit $LASTEXITCODE
}

Start-LoggedProcess `
    -FilePath $uvicorn `
    -ArgumentList $apiArgs `
    -WorkingDirectory (Join-Path $repoRoot 'apps\api') `
    -StdoutPath $apiOut `
    -StderrPath $apiErr `
    -PidPath $apiPidFile `
    -Label 'API (uvicorn)' | Out-Null

if (-not $NoWeb) {
    Assert-Command -Name 'npm' -Hint 'Install Node 20+ / run setup-native.ps1.' | Out-Null
    Start-LoggedProcess `
        -FilePath "$env:ComSpec" `
        -ArgumentList @('/c', 'npm', 'run', 'dev') `
        -WorkingDirectory (Join-Path $repoRoot 'apps\portal-web') `
        -StdoutPath $webOut `
        -StderrPath $webErr `
        -PidPath $webPidFile `
        -Label 'portal-web (Vite)' | Out-Null
}

if ($WithWorker) {
    Start-LoggedProcess `
        -FilePath $python `
        -ArgumentList @('-m', 'app.main') `
        -WorkingDirectory (Join-Path $repoRoot 'apps\worker') `
        -StdoutPath $workerOut `
        -StderrPath $workerErr `
        -PidPath $workerPidFile `
        -Label 'worker' | Out-Null
}

Start-Sleep -Seconds 2

Write-Host ''
Write-Host 'Native stack started (dry_run).'
Write-Host '  UI:     http://127.0.0.1:5173'
Write-Host "  API:    http://127.0.0.1:$port/docs"
Write-Host "  Health: http://127.0.0.1:$port/health/ready"
Write-Host 'Stop with: .\deploy\windows\native\stop-native.ps1'
Write-Host "Logs: $logsDir"
