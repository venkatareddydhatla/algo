<#
.SYNOPSIS
  One-time native (Docker-free) setup for Options Portal on Windows.

.DESCRIPTION
  - Creates Python venv at repo\.venv
  - pip installs editable packages (API, worker, plugins)
  - npm install for apps\portal-web
  - Copies .env.native.example -> .env if .env is missing (SQLite / local profile)
  - Ensures data\ directory for the SQLite file

  Does NOT require Docker, WSL, Virtual Machine Platform, Postgres, or Redis.

.PARAMETER SkipNpm
  Skip portal-web npm install.

.PARAMETER SkipPip
  Skip Python venv / pip (use existing .venv).

.PARAMETER AllowUnsupportedPython
  Allow Python 3.15+ (or other versions without wheels). Those builds usually need MSVC Build Tools.
  Prefer Python 3.12.x instead.

.EXAMPLE
  cd C:\algo
  Set-ExecutionPolicy -Scope Process Bypass
  .\deploy\windows\native\setup-native.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipNpm,
    [switch]$SkipPip,
    [switch]$AllowUnsupportedPython
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_native-common.ps1')

$repoRoot = Get-NativeRepoRoot
Write-Host "Repo root: $repoRoot"
Set-Location $repoRoot

$hostPython = Assert-PythonVersion -AllowUnsupportedPython:$AllowUnsupportedPython
if (-not $SkipNpm) {
    Assert-NodeVersion | Out-Null
}

Ensure-NativeDotEnv -RepoRoot $repoRoot | Out-Null

$dataDir = Join-Path $repoRoot 'data'
if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    Write-Host "Created data\ for SQLite file."
}
Get-NativeLogsDir -RepoRoot $repoRoot | Out-Null

if (-not $SkipPip) {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "Creating Python venv at .venv using $hostPython ..."
        & $hostPython -m venv (Join-Path $repoRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed with code $LASTEXITCODE" }
    } else {
        Write-Host "Using existing venv: $venvPython"
    }

    $pip = Join-Path $repoRoot '.venv\Scripts\python.exe'
    Write-Host "Upgrading pip / setuptools / wheel ..."
    & $pip -m pip install -U pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

    # Pre-install PyYAML from binary wheels only so editable installs do not compile
    # (fails on Windows without MSVC, especially on Python 3.13+ / 3.15 with no wheels).
    Write-Host 'Pre-installing PyYAML (binary wheel only) ...'
    & $pip -m pip install --only-binary=:all: "PyYAML>=6.0,<7"
    if ($LASTEXITCODE -ne 0) {
        throw @"
Failed to install a binary wheel for PyYAML on this Python.
This usually means you are on Python 3.13+ / 3.15 (no wheel yet) and pip would need Microsoft Visual C++ 14.0 to build from source.

Fix (recommended):
  1) Install Python 3.12.x from https://www.python.org/downloads/ (Add to PATH)
  2) From the repo root:
       Remove-Item -Recurse -Force .venv
       py -3.12 --version
       .\deploy\windows\native\setup-native.ps1

Advanced: install Build Tools and re-run with -AllowUnsupportedPython (not needed on 3.12).
"@
    }
    Write-Host "PyYAML binary wheel installed."

    Write-Host 'Installing editable packages (this may take a few minutes) ...'
    $packages = @(
        '.\packages\domain',
        '.\packages\broker-upstox',
        '.\packages\ta-core',
        '.\packages\news-rss',
        '.\packages\sentiment',
        '.\packages\hybrid-engine',
        '.\packages\risk',
        '.\packages\notify',
        '.\packages\persistence',
        '.\packages\backtest',
        '.\apps\api',
        '.\apps\worker'
    )
    $pipArgs = @()
    foreach ($pkg in $packages) {
        $pipArgs += '-e'
        $pipArgs += $pkg
    }
    $pipArgs += @('pytest', 'pytest-asyncio')
    & $pip -m pip install @pipArgs
    if ($LASTEXITCODE -ne 0) { throw "pip install -e packages failed with code $LASTEXITCODE" }
    Write-Host "Python packages installed."
}

if (-not $SkipNpm) {
    $webDir = Join-Path $repoRoot 'apps\portal-web'
    if (-not (Test-Path -LiteralPath (Join-Path $webDir 'package.json'))) {
        throw "portal-web package.json not found at $webDir"
    }
    Write-Host "npm install in apps\portal-web ..."
    Push-Location $webDir
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed with code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
    Write-Host "npm install complete."
}

Write-Host ''
Write-Host 'Setup complete (native / Docker-free).'
Write-Host 'Next:'
Write-Host '  .\deploy\windows\native\start-native.ps1'
Write-Host '  # optional worker: .\deploy\windows\native\start-native.ps1 -WithWorker'
Write-Host 'URLs after start: UI http://127.0.0.1:5173  API http://127.0.0.1:8000/docs'
Write-Host 'Trading mode: dry_run (default).'
