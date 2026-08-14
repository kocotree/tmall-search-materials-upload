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
if (-not [System.IO.File]::Exists($Python)) {
    throw "Prepared user runtime Python is missing."
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
