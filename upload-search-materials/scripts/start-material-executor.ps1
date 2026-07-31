param(
    [string]$Session = "",
    [string]$Config = "",
    [int]$IdleTimeout = 600
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..")
)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
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
