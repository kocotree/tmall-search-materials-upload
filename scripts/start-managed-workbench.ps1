param(
    [string]$RunsRoot = "",
    [string]$Session = "",
    [int]$PortStart = 8765,
    [int]$PortEnd = 8795,
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..")
)
$env:TMALL_PLUGIN_ROOT = $ProjectRoot
$env:TMALL_WORKSPACE_ROOT = $ProjectRoot
$RuntimeRoot = if ($env:TMALL_RUNTIME_ROOT) {
    [System.IO.Path]::GetFullPath($env:TMALL_RUNTIME_ROOT)
}
elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "tmall-search-materials\runtime"
}
else {
    Join-Path $HOME ".local\state\tmall-search-materials\runtime"
}
$Python = Join-Path $RuntimeRoot ".venv\Scripts\python.exe"
$LockPath = Join-Path $ProjectRoot "uv.lock"
$FingerprintPath = Join-Path $RuntimeRoot "environment-fingerprint.json"
$EnvironmentReady = (
    [System.IO.File]::Exists($Python) -and
    [System.IO.File]::Exists($FingerprintPath)
)
if ($EnvironmentReady) {
    try {
        $Fingerprint = Get-Content -LiteralPath $FingerprintPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $CurrentLock = (Get-FileHash -LiteralPath $LockPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $EnvironmentReady = (
            [int]$Fingerprint.schema_version -eq 2 -and
            [string]$Fingerprint.lock_sha256 -eq $CurrentLock -and
            [string]$Fingerprint.dependency_mode -eq "no-install-project" -and
            [string]$Fingerprint.launch_mode -eq "current-plugin-source"
        )
    }
    catch {
        $EnvironmentReady = $false
    }
}
if (-not $EnvironmentReady) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "RUNTIME_BOOTSTRAP_FAILED: bootstrap did not complete."
    }
}
if (-not [System.IO.File]::Exists($Python)) {
    throw "RUNTIME_BOOTSTRAP_FAILED: prepared user runtime Python is missing."
}

$Arguments = @(
    (Join-Path $PSScriptRoot "run-plugin.py"),
    "desktop-workbench",
    "--project-root", $ProjectRoot,
    "--port-start", [string]$PortStart,
    "--port-end", [string]$PortEnd
)
if ($RunsRoot) {
    $Arguments += @("--runs-root", $RunsRoot)
}
if ($Session) {
    $Arguments += @("--session", $Session)
}
if ($Config) {
    $Arguments += @("--config", $Config)
}

& $Python @Arguments
exit $LASTEXITCODE
