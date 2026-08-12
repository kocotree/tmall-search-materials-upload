param(
    [string]$Session = "",
    [string]$Config = "",
    [int]$IdleTimeout = 600
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
if (-not [System.IO.File]::Exists($Python)) {
    throw "Prepared project Python is missing."
}

$Arguments = @(
    "-m", "upload_search_materials.cli",
    "material-executor",
    "--watch",
    "--idle-timeout", [string]$IdleTimeout
)
if ($Session) {
    $Arguments += @("--session", $Session)
}
if ($Config) {
    $Arguments += @("--config", $Config)
}

& $Python @Arguments
exit $LASTEXITCODE
