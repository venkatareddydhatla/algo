# Shared helpers for Options Portal Windows scripts.
# Dot-source from sibling scripts only. Not meant to be run directly.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Capture at dot-source time (deploy/windows). Do not rely on $PSScriptRoot inside later callers.
$script:WindowsDeployDir = $PSScriptRoot
$script:ServiceName = 'OptionsPortal'
$script:ComposeRelPath = 'deploy/docker-compose.yml'

function Get-RepoRoot {
    # deploy/windows -> repo root
    return (Resolve-Path (Join-Path $script:WindowsDeployDir '..\..')).Path
}

function Get-ComposeFile {
    param([string]$RepoRoot = (Get-RepoRoot))
    return (Join-Path $RepoRoot $script:ComposeRelPath)
}

function Test-IsAdministrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Administrator {
    param([string]$Action = 'this operation')
    if (-not (Test-IsAdministrator)) {
        throw "Administrator privileges required for $Action. Re-open PowerShell as Administrator and retry."
    }
}

function Ensure-DotEnv {
    param([string]$RepoRoot = (Get-RepoRoot))
    $envPath = Join-Path $RepoRoot '.env'
    $examplePath = Join-Path $RepoRoot '.env.example'
    if (Test-Path -LiteralPath $envPath) {
        Write-Host "Using existing .env at $envPath (secrets not overwritten)."
        return $envPath
    }
    if (-not (Test-Path -LiteralPath $examplePath)) {
        throw "Missing both .env and .env.example under $RepoRoot"
    }
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Host "Created .env from .env.example at $envPath"
    Write-Host "Edit POSTGRES_PASSWORD / SESSION_SECRET before first production-like use. TRADING_MODE stays dry_run by default."
    return $envPath
}

function Assert-DockerAvailable {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        throw "docker not found on PATH. Install Docker Desktop for Windows and ensure it is running."
    }
    try {
        & docker info 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "docker info failed"
        }
    } catch {
        throw "Docker Desktop does not appear to be running (docker info failed). Start Docker Desktop, wait until it is ready, then retry."
    }
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ComposeArgs,
        [string]$RepoRoot = (Get-RepoRoot)
    )
    Assert-DockerAvailable
    $composeFile = Get-ComposeFile -RepoRoot $RepoRoot
    if (-not (Test-Path -LiteralPath $composeFile)) {
        throw "Compose file not found: $composeFile"
    }
    Push-Location $RepoRoot
    try {
        & docker compose -f $composeFile @ComposeArgs
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose exited with code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function Get-DockerExePath {
    $cmd = Get-Command docker -ErrorAction Stop
    return $cmd.Source
}

function Find-Nssm {
    param(
        [string]$NssmPath,
        [string]$RepoRoot = (Get-RepoRoot)
    )
    if ($NssmPath) {
        if (-not (Test-Path -LiteralPath $NssmPath)) {
            throw "NSSM not found at -NssmPath: $NssmPath"
        }
        return (Resolve-Path -LiteralPath $NssmPath).Path
    }
    $local = Join-Path $RepoRoot 'deploy\windows\tools\nssm\win64\nssm.exe'
    if (Test-Path -LiteralPath $local) {
        return (Resolve-Path -LiteralPath $local).Path
    }
    $onPath = Get-Command nssm -ErrorAction SilentlyContinue
    if ($onPath) {
        return $onPath.Source
    }
    return $null
}

function Get-ServiceState {
    param([string]$Name = $script:ServiceName)
    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $svc) { return $null }
    return $svc.Status.ToString()
}
