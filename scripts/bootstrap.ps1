[CmdletBinding()]
param(
    [ValidateSet("auto", "official", "tuna", "aliyun", "tencent")]
    [string]$Mirror = "auto",

    [string]$Python = $env:TMALL_PYTHON,

    [switch]$UpdateLock,

    [switch]$WithTests
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$officialConfigFile = Join-Path $projectRoot "config\uv-official.toml"
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
    "--config-file", $officialConfigFile,
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
        if ($Mirror -eq "auto") {
            throw "LOCK_UPDATE_MIRROR_REQUIRED: pass an explicit -Mirror when updating uv.lock."
        }
        $lockConfigFile = Join-Path $projectRoot "config\uv-$Mirror.toml"
        & $uvExecutable --config-file $lockConfigFile --cache-dir $cacheDir lock `
            --python $Python `
            --no-managed-python `
            --system-certs
        if ($LASTEXITCODE -ne 0) {
            throw "LOCK_UPDATE_FAILED: uv could not resolve dependencies with mirror '$Mirror'."
        }
    }

    $bootstrapMetadataDir = Join-Path $runtimeRoot "bootstrap"
    $requirementsFile = Join-Path $bootstrapMetadataDir "requirements.locked.txt"
    New-Item -ItemType Directory -Path $bootstrapMetadataDir -Force | Out-Null
    $exportArguments = @(
        "export",
        "--locked",
        "--no-emit-project",
        "--no-dev",
        "--format", "requirements-txt",
        "--output-file", $requirementsFile,
        "--python", $Python,
        "--no-managed-python",
        "--system-certs"
    )
    if ($WithTests) {
        $exportArguments += @("--extra", "test")
    }

    $exportConfigFile = if ($UpdateLock) {
        $lockConfigFile
    }
    else {
        $officialConfigFile
    }
    & $uvExecutable --config-file $exportConfigFile --cache-dir $cacheDir @exportArguments
    if ($LASTEXITCODE -ne 0) {
        throw "DEPENDENCY_EXPORT_FAILED: uv.lock could not be exported."
    }

    $runtimePython = Join-Path $environmentDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        & $uvExecutable @commonArguments venv $environmentDir `
            --python $Python `
            --no-managed-python `
            --system-certs
        if ($LASTEXITCODE -ne 0) {
            throw "RUNTIME_VENV_FAILED: uv could not create the user runtime environment."
        }
    }

    $mirrorOrder = if ($Mirror -eq "auto") {
        @("tuna", "official")
    }
    else {
        @($Mirror)
    }
    $syncSucceeded = $false
    $failedMirrors = @()
    foreach ($mirrorName in $mirrorOrder) {
        $configFile = Join-Path $projectRoot "config\uv-$mirrorName.toml"
        Write-Host "Synchronizing locked dependencies from '$mirrorName'."
        & $uvExecutable --config-file $configFile --cache-dir $cacheDir pip sync `
            $requirementsFile `
            --python $runtimePython `
            --no-managed-python `
            --system-certs
        if ($LASTEXITCODE -eq 0) {
            $syncSucceeded = $true
            break
        }
        $failedMirrors += $mirrorName
        if ($Mirror -eq "auto" -and $mirrorName -ne $mirrorOrder[-1]) {
            Write-Warning "Dependency download from '$mirrorName' failed; retrying with the official index."
        }
    }
    if (-not $syncSucceeded) {
        $attempted = $failedMirrors -join ", "
        throw "DEPENDENCY_SYNC_FAILED: locked dependencies could not be downloaded from: $attempted."
    }

    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        throw "RUNTIME_SMOKE_TEST_FAILED: prepared Python was not installed."
    }

    $pluginLauncher = Join-Path $PSScriptRoot "run-plugin.py"
    & $runtimePython $pluginLauncher --help
    if ($LASTEXITCODE -ne 0) {
        throw "RUNTIME_SMOKE_TEST_FAILED: dependencies installed, but current Plugin source did not start."
    }

    & $runtimePython (Join-Path $PSScriptRoot "write-runtime-fingerprint.py") `
        --project-root $projectRoot `
        --runtime-root $runtimeRoot `
        --python $runtimePython `
        --uv-cache-dir $cacheDir
    if ($LASTEXITCODE -ne 0) {
        throw "RUNTIME_FINGERPRINT_FAILED: prepared dependency environment could not be recorded."
    }
}
finally {
    Pop-Location
}
