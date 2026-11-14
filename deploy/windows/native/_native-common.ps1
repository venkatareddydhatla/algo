# Shared helpers for Options Portal native (Docker-free) Windows scripts.
# Dot-source from sibling scripts only. Not meant to be run directly.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:NativeDeployDir = $PSScriptRoot
$script:NativeServiceName = 'OptionsPortalApiNative'

function Get-NativeRepoRoot {
    # deploy/windows/native -> repo root
    return (Resolve-Path (Join-Path $script:NativeDeployDir '..\..\..')).Path
}

function Get-NativeLogsDir {
    param([string]$RepoRoot = (Get-NativeRepoRoot))
    $dir = Join-Path $RepoRoot 'deploy\windows\native\logs'
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    return $dir
}

function Get-VenvPython {
    param([string]$RepoRoot = (Get-NativeRepoRoot))
    $py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        throw "Python venv not found at $py. Run setup-native.ps1 first."
    }
    return (Resolve-Path -LiteralPath $py).Path
}

function Get-VenvUvicorn {
    param([string]$RepoRoot = (Get-NativeRepoRoot))
    $uv = Join-Path $RepoRoot '.venv\Scripts\uvicorn.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        throw "uvicorn not found at $uv. Run setup-native.ps1 first."
    }
    return (Resolve-Path -LiteralPath $uv).Path
}

function Assert-Command {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Hint
    )
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $msg = "Required command '$Name' not found on PATH."
        if ($Hint) { $msg = "$msg $Hint" }
        throw $msg
    }
    return $cmd
}

function Resolve-HostPython {
    <#
    .SYNOPSIS
      Prefer Python 3.12 via the `py` launcher, then other real installs over the WindowsApps Store stub.
    .OUTPUTS
      Full path to python.exe that can run -m venv.
    #>
    $candidates = @()

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        # Prefer 3.12 (binary wheels for PyYAML etc.), then 3.11, then latest -3.
        foreach ($tag in @('-3.12', '-3.11', '-3')) {
            try {
                $exe = (& $pyLauncher.Source $tag -c "import sys; print(sys.executable)").Trim()
                if ($exe -and (Test-Path -LiteralPath $exe) -and ($exe -notmatch '\\WindowsApps\\')) {
                    if ($candidates -notcontains $exe) {
                        $candidates += $exe
                    }
                }
            } catch {
                # tag not installed — continue
            }
        }
    }

    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -match '\\WindowsApps\\') { continue }
        if ($candidates -notcontains $cmd.Source) {
            $candidates += $cmd.Source
        }
    }

    if (-not $candidates.Count) {
        throw "No usable Python found. Install Python 3.12 from https://www.python.org/downloads/ (Add to PATH), or use the `py` launcher (`py -3.12`). Disable Settings > Apps > Advanced app settings > App execution aliases for python.exe / python3.exe if the Store stub shadows PATH."
    }
    return $candidates[0]
}

function Assert-PythonVersion {
    param(
        [version]$Minimum = [version]'3.12.0',
        [switch]$AllowUnsupportedPython
    )
    $pythonExe = Resolve-HostPython
    $verText = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to query Python version via $pythonExe"
    }
    $ver = [version]$verText.Trim()
    if ($ver -lt $Minimum) {
        throw "Python $Minimum+ required (found $ver at $pythonExe). Install Python 3.12.x from https://www.python.org/downloads/ (check Add to PATH), then re-run. Tip: `py -3.12` should work after install."
    }

    # 3.15+ almost never has binary wheels yet (PyYAML needs MSVC). Reject unless opted in.
    if ($ver.Major -gt 3 -or ($ver.Major -eq 3 -and $ver.Minor -ge 15)) {
        $msg = @"
Python $ver is not supported for native Windows setup without compilers.
PyYAML and other deps usually lack wheels on 3.15+, so pip tries to build from source and fails with 'Microsoft Visual C++ 14.0 is required'.
Install Python 3.12.x from https://www.python.org/downloads/, remove .venv, and re-run setup-native.ps1.
To force this interpreter anyway (requires MSVC Build Tools): pass -AllowUnsupportedPython.
"@
        if (-not $AllowUnsupportedPython) {
            throw $msg.Trim()
        }
        Write-Warning $msg.Trim()
    } elseif ($ver.Major -eq 3 -and $ver.Minor -ge 13) {
        Write-Warning "Python $ver may lack prebuilt wheels for some packages (e.g. PyYAML). Prefer Python 3.12.x to avoid needing Microsoft Visual C++ Build Tools. Continuing..."
    } elseif ($ver.Major -eq 3 -and $ver.Minor -eq 12) {
        Write-Host "Python $ver (recommended) at $pythonExe"
        return $pythonExe
    }

    Write-Host "Python $ver at $pythonExe"
    return $pythonExe
}

function Assert-NodeVersion {
    param([version]$Minimum = [version]'20.0.0')
    $node = Assert-Command -Name 'node' -Hint 'Install Node.js 20+ LTS from https://nodejs.org/'
    $verText = (& $node.Source -v).Trim().TrimStart('v')
    $ver = [version]$verText
    if ($ver -lt $Minimum) {
        throw "Node.js $Minimum+ required (found $ver). Install Node 20+ LTS."
    }
    Assert-Command -Name 'npm' -Hint 'npm normally ships with Node.js.' | Out-Null
    Write-Host "Node $ver at $($node.Source)"
    return $node.Source
}

function Ensure-NativeDotEnv {
    param([string]$RepoRoot = (Get-NativeRepoRoot))
    $envPath = Join-Path $RepoRoot '.env'
    $nativeExample = Join-Path $RepoRoot '.env.native.example'
    $examplePath = Join-Path $RepoRoot '.env.example'

    if (Test-Path -LiteralPath $envPath) {
        Write-Host "Using existing .env at $envPath (secrets not overwritten)."
        return $envPath
    }

    if (Test-Path -LiteralPath $nativeExample) {
        Copy-Item -LiteralPath $nativeExample -Destination $envPath
        Write-Host "Created .env from .env.native.example (SQLite / local profile)."
    } elseif (Test-Path -LiteralPath $examplePath) {
        Copy-Item -LiteralPath $examplePath -Destination $envPath
        # Patch in local defaults so first boot does not need Postgres/Redis
        $lines = Get-Content -LiteralPath $envPath
        $patched = @()
        $hasProfile = $false
        foreach ($line in $lines) {
            if ($line -match '^\s*DATABASE_URL=') {
                $patched += 'DATABASE_URL=sqlite:///./data/options_portal.local.sqlite'
                continue
            }
            if ($line -match '^\s*REDIS_URL=') {
                $patched += 'REDIS_URL=memory://local'
                continue
            }
            if ($line -match '^\s*PERSISTENCE_PROFILE=') {
                $patched += 'PERSISTENCE_PROFILE=local'
                $hasProfile = $true
                continue
            }
            if ($line -match '^\s*POSTGRES_PASSWORD=') {
                # Not required for native SQLite; keep as comment
                $patched += '# POSTGRES_PASSWORD= (not required for native SQLite profile)'
                continue
            }
            $patched += $line
        }
        if (-not $hasProfile) {
            $patched = @('PERSISTENCE_PROFILE=local') + $patched
        }
        Set-Content -LiteralPath $envPath -Value $patched -Encoding UTF8
        Write-Host "Created .env from .env.example with SQLite-friendly local defaults."
    } else {
        throw "Missing .env, .env.native.example, and .env.example under $RepoRoot"
    }

    Write-Host "TRADING_MODE stays dry_run by default. Edit SESSION_SECRET before sharing the host."
    return $envPath
}

function Import-DotEnvFile {
    param(
        [Parameter(Mandatory = $true)][string]$EnvPath
    )
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        throw ".env not found: $EnvPath"
    }
    Get-Content -LiteralPath $EnvPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($key, $val, 'Process')
        Set-Item -Path "Env:$key" -Value $val
    }
}

function Read-PidFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $raw = (Get-Content -LiteralPath $Path -Raw).Trim()
    if (-not $raw) { return $null }
    $id = 0
    if (-not [int]::TryParse($raw, [ref]$id)) { return $null }
    return $id
}

function Stop-PidFileProcess {
    param(
        [Parameter(Mandatory = $true)][string]$PidFile,
        [string]$Label = 'process'
    )
    $id = Read-PidFile -Path $PidFile
    if ($null -eq $id) {
        Write-Host "No $Label PID file at $PidFile"
        return
    }
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Stopping $Label PID $id ($($proc.ProcessName))..."
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
    } else {
        Write-Host "$Label PID $id is not running."
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Test-IsAdministrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Nssm {
    param(
        [string]$NssmPath,
        [string]$RepoRoot = (Get-NativeRepoRoot)
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
    if ($onPath) { return $onPath.Source }
    return $null
}
