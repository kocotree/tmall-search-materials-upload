[CmdletBinding()]
param(
    [ValidateSet("official", "tuna", "aliyun", "tencent")]
    [string]$Mirror = "official",

    [string]$Python = $env:TMALL_PYTHON,

    [switch]$UpdateLock,

    [switch]$WithTests
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$configFile = Join-Path $projectRoot "config\uv-$Mirror.toml"
$runtimeRoot = if ($env:TMALL_RUNTIME_ROOT) {
    [System.IO.Path]::GetFullPath($env:TMALL_RUNTIME_ROOT)
}
elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "tmall-search-materials\runtime"
}
else {
    Join-Path $HOME ".local\state\tmall-search-materials\runtime"
}
$environmentDir = Join-Path $runtimeRoot ".venv"
$cacheDir = Join-Path $runtimeRoot "uv-cache"
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$env:UV_PROJECT_ENVIRONMENT = $environmentDir
$commonArguments = @(
    "--config-file", $configFile,
    "--cache-dir", $cacheDir
)

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uvExecutable = $uvCommand.Source
}
else {
    $uvCandidates = Get-ChildItem `
        -Path (Join-Path $env:APPDATA "Python") `
        -Filter "uv.exe" `
        -Recurse `
        -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    $uvExecutable = $uvCandidates |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $uvExecutable) {
    throw "UV_NOT_FOUND: install uv first, then rerun this script."
}

if (-not $Python) {
    $PythonResult = & $uvExecutable @commonArguments python find --no-managed-python ">=3.11" 2>$null
    $Python = if ($PythonResult) { ([string]$PythonResult).Trim() } else { "" }
    if ($LASTEXITCODE -ne 0 -or -not $Python) {
        & $uvExecutable @commonArguments python install 3.11 --default --system-certs
        if ($LASTEXITCODE -ne 0) {
            throw "PYTHON_INSTALL_FAILED: install Python 3.11 or pass -Python."
        }
        $Python = (& $uvExecutable @commonArguments python find "3.11").Trim()
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "PYTHON_NOT_AVAILABLE: '$Python' is not an executable file."
}

Push-Location $projectRoot
try {
    if ($UpdateLock) {
        & $uvExecutable @commonArguments lock `
            --python $Python `
            --no-managed-python `
            --system-certs
        if ($LASTEXITCODE -ne 0) {
            throw "LOCK_UPDATE_FAILED: uv could not resolve dependencies with mirror '$Mirror'."
        }
    }

    $syncArguments = @(
        "sync",
        "--locked",
        "--no-editable",
        "--python", $Python,
        "--no-managed-python",
        "--system-certs"
    )
    if ($WithTests) {
        $syncArguments += @("--extra", "test")
    }

    & $uvExecutable @commonArguments @syncArguments
    if ($LASTEXITCODE -ne 0) {
        throw "DEPENDENCY_SYNC_FAILED: the lockfile may belong to another mirror. Rerun with -UpdateLock only when intentionally changing the lock source."
    }

    $cliExecutable = Join-Path $environmentDir "Scripts\tmall-materials.exe"
    if (-not (Test-Path -LiteralPath $cliExecutable -PathType Leaf)) {
        throw "CLI_SMOKE_TEST_FAILED: tmall-materials entry point was not installed."
    }

    & $cliExecutable --help
    if ($LASTEXITCODE -ne 0) {
        throw "CLI_SMOKE_TEST_FAILED: dependencies installed, but tmall-materials did not start."
    }

    $fingerprintPath = Join-Path $runtimeRoot "environment-fingerprint.json"
    $lockPath = Join-Path $projectRoot "uv.lock"
    $fingerprint = [ordered]@{
        schema_version = 1
        lock_sha256 = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
        python = (Resolve-Path -LiteralPath $Python).Path
        executable = (Resolve-Path -LiteralPath $cliExecutable).Path
        launch_identity = (Resolve-Path -LiteralPath $cliExecutable).Path
        uv_cache_dir = (Resolve-Path -LiteralPath $cacheDir).Path
        prepared_at = [DateTimeOffset]::Now.ToString("o")
    }
    $temporaryFingerprint = "$fingerprintPath.tmp"
    $fingerprint | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath $temporaryFingerprint -Encoding UTF8
    Move-Item -LiteralPath $temporaryFingerprint -Destination $fingerprintPath -Force
}
finally {
    Pop-Location
}
