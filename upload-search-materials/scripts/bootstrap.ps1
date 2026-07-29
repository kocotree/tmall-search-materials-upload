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
$cacheDir = Join-Path $projectRoot ".uv-cache"

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
    $Python = (& $uvExecutable python find --no-managed-python ">=3.11").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Python) {
        throw "PYTHON_NOT_AVAILABLE: set TMALL_PYTHON or pass -Python with a Python >=3.11 executable."
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "PYTHON_NOT_AVAILABLE: '$Python' is not an executable file."
}

$commonArguments = @(
    "--config-file", $configFile,
    "--cache-dir", $cacheDir
)

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

    $cliExecutable = Join-Path $projectRoot ".venv\Scripts\tmall-materials.exe"
    if (-not (Test-Path -LiteralPath $cliExecutable -PathType Leaf)) {
        throw "CLI_SMOKE_TEST_FAILED: tmall-materials entry point was not installed."
    }

    & $cliExecutable --help
    if ($LASTEXITCODE -ne 0) {
        throw "CLI_SMOKE_TEST_FAILED: dependencies installed, but tmall-materials did not start."
    }

    $fingerprintPath = Join-Path $projectRoot ".environment-fingerprint.json"
    $lockPath = Join-Path $projectRoot "uv.lock"
    $fingerprint = [ordered]@{
        schema_version = 1
        lock_sha256 = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
        python = (Resolve-Path -LiteralPath $Python).Path
        executable = (Resolve-Path -LiteralPath $cliExecutable).Path
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
