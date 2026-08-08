param(
    [Parameter(Mandatory = $true)]
    [string]$RunsRoot,
    [string]$Session = "",
    [int]$PortStart = 8765,
    [int]$PortEnd = 8795,
    [string]$Config = ""
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
    "desktop-workbench",
    "--runs-root", $RunsRoot,
    "--port-start", [string]$PortStart,
    "--port-end", [string]$PortEnd
)
if ($Session) {
    $Arguments += @("--session", $Session)
}
if ($Config) {
    $Arguments += @("--config", $Config)
}

& $Python @Arguments
exit $LASTEXITCODE
