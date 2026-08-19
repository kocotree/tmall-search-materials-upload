[CmdletBinding()]
param(
    [string]$Session,
    [string]$RunsRoot,
    [string]$Config,
    [ValidateSet("auto", "official", "tuna", "aliyun", "tencent")]
    [string]$Mirror = "auto",
    [int]$PortStart = 8765,
    [int]$PortEnd = 8795,
    [switch]$OpenSystemBrowser,
    [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent $PSScriptRoot
$env:TMALL_PLUGIN_ROOT = $skillRoot
$env:TMALL_WORKSPACE_ROOT = $skillRoot
$runtimeRoot = if ($env:TMALL_RUNTIME_ROOT) {
    [System.IO.Path]::GetFullPath($env:TMALL_RUNTIME_ROOT)
}
elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "tmall-search-materials\runtime"
}
else {
    Join-Path $HOME ".local\state\tmall-search-materials\runtime"
}
$pythonExecutable = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
$sourcePackage = Join-Path $skillRoot "src\upload_search_materials"
$pluginLauncher = Join-Path $PSScriptRoot "run-plugin.py"
$lockPath = Join-Path $skillRoot "uv.lock"
$fingerprintPath = Join-Path $runtimeRoot "environment-fingerprint.json"

$runtimeExecutable = $pythonExecutable
$runtimePrefix = @($pluginLauncher)

$environmentReady = (
    (Test-Path -LiteralPath $runtimeExecutable -PathType Leaf) -and
    (Test-Path -LiteralPath $sourcePackage -PathType Container) -and
    (Test-Path -LiteralPath $pluginLauncher -PathType Leaf)
)
if ($environmentReady -and (Test-Path -LiteralPath $fingerprintPath -PathType Leaf)) {
    try {
        $fingerprint = Get-Content -LiteralPath $fingerprintPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $currentLock = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $environmentReady = (
            [int]$fingerprint.schema_version -eq 2 -and
            [string]$fingerprint.lock_sha256 -eq $currentLock -and
            [string]$fingerprint.dependency_mode -eq "no-install-project" -and
            [string]$fingerprint.launch_mode -eq "current-plugin-source"
        )
    }
    catch {
        $environmentReady = $false
    }
}
elseif ($environmentReady) {
    & $runtimeExecutable @runtimePrefix environment-status --project-root $skillRoot | Out-Null
    $environmentReady = $LASTEXITCODE -eq 0
}

if (-not $environmentReady) {
    if ($SkipBootstrap) {
        throw "RUNTIME_NOT_READY: project environment is missing or does not match uv.lock."
    }
    & (Join-Path $PSScriptRoot "bootstrap.ps1") -Mirror $Mirror
    if ($LASTEXITCODE -ne 0) {
        throw "RUNTIME_BOOTSTRAP_FAILED: bootstrap did not complete."
    }
    $runtimeExecutable = $pythonExecutable
    $runtimePrefix = @($pluginLauncher)
}

$arguments = @("ui-start", "--port-start", $PortStart, "--port-end", $PortEnd)
if ($Session) { $arguments += @("--session", $Session) }
if ($RunsRoot) { $arguments += @("--runs-root", $RunsRoot) }
if ($Config) { $arguments += @("--config", $Config) }
if ($OpenSystemBrowser) { $arguments += "--open-system-browser" }

& $runtimeExecutable @runtimePrefix @arguments
exit $LASTEXITCODE
