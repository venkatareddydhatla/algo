<#
.SYNOPSIS
  Optional NSSM Windows service for the **native API only** (no Docker).

.DESCRIPTION
  Installs service OptionsPortalApiNative that runs uvicorn from the repo .venv.
  Prefer start-native.ps1 for day-to-day use (API + Vite). Use this only if you
  want the API as a Manual Windows service without Docker Desktop.

  Requires: setup-native.ps1 already run; Administrator PowerShell; NSSM.

.PARAMETER DownloadNssm
  Download NSSM 2.24 into deploy\windows\tools\ (same as Docker path).

.PARAMETER NssmPath
  Path to nssm.exe if already installed.

.PARAMETER AutoStart
  Set service start type to Automatic (default is Manual/demand).

.EXAMPLE
  .\deploy\windows\native\install-native-api-service.ps1 -DownloadNssm
#>
[CmdletBinding()]
param(
    [switch]$DownloadNssm,
    [string]$NssmPath,
    [switch]$AutoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_native-common.ps1')

if (-not (Test-IsAdministrator)) {
    throw "Administrator privileges required. Re-open PowerShell as Administrator."
}

$repoRoot = Get-NativeRepoRoot
Ensure-NativeDotEnv -RepoRoot $repoRoot | Out-Null
$uvicorn = Get-VenvUvicorn -RepoRoot $repoRoot
$logsDir = Get-NativeLogsDir -RepoRoot $repoRoot
$apiAppDir = Join-Path $repoRoot 'apps\api'
$dataDir = Join-Path $repoRoot 'data'
if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}
$sqliteFile = Join-Path $dataDir 'options_portal.local.sqlite'
$sqliteUrl = 'sqlite:///' + ($sqliteFile -replace '\\', '/')
$configPath = Join-Path $repoRoot 'config\local.yaml'

$nssm = Find-Nssm -NssmPath $NssmPath -RepoRoot $repoRoot
if (-not $nssm) {
    if ($DownloadNssm) {
        $url = 'https://nssm.cc/release/nssm-2.24.zip'
        $tools = Join-Path $repoRoot 'deploy\windows\tools'
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
            throw "nssm.exe (win64) not found in downloaded zip."
        }
        if (Test-Path -LiteralPath $dest) {
            Remove-Item -LiteralPath $dest -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Copy-Item -LiteralPath $win64.DirectoryName -Destination (Join-Path $dest 'win64') -Recurse -Force
        $nssm = Join-Path $dest 'win64\nssm.exe'
    } else {
        throw "NSSM not found. Pass -DownloadNssm or -NssmPath. Or use start-native.ps1 instead of a service."
    }
}

$svc = $script:NativeServiceName
$existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host ('Service ''{0}'' already exists (status {1}). Remove with uninstall-native-api-service.ps1 first if reinstalling.' -f $svc, $existing.Status)
    return
}

$hostBind = '127.0.0.1'
$port = '8000'
Write-Host "Installing service '$svc' -> $uvicorn"
& $nssm install $svc $uvicorn
if ($LASTEXITCODE -ne 0) { throw "nssm install failed: $LASTEXITCODE" }

& $nssm set $svc AppDirectory $apiAppDir
& $nssm set $svc AppParameters "app.main:app --host $hostBind --port $port"
& $nssm set $svc AppStdout (Join-Path $logsDir 'service-api-stdout.log')
& $nssm set $svc AppStderr (Join-Path $logsDir 'service-api-stderr.log')
# Extra env: local SQLite profile, dry_run only
$envExtra = @(
    "PERSISTENCE_PROFILE=local",
    "TRADING_MODE=dry_run",
    "CONFIG_PATH=$configPath",
    "DATABASE_URL=$sqliteUrl",
    "REDIS_URL=memory://local",
    "BIND_HOST=$hostBind",
    "API_PORT=$port"
) -join ' '
& $nssm set $svc AppEnvironmentExtra $envExtra

if ($AutoStart) {
    & $nssm set $svc Start SERVICE_AUTO_START
} else {
    & $nssm set $svc Start SERVICE_DEMAND_START
}

Write-Host "Installed. Start with: Start-Service $svc"
Write-Host 'Or prefer full stack (API+UI): .\deploy\windows\native\start-native.ps1'
Write-Host 'UI still needs Vite via start-native.ps1 (or npm run dev) - this service is API-only.'
