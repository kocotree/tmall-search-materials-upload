[CmdletBinding()]
param(
    [string]$Session,
    [string]$RunsRoot,
    [string]$Config,
    [ValidateSet("official", "tuna", "aliyun", "tencent")]
    [string]$Mirror = "official",
    [int]$PortStart = 8765,
    [int]$PortEnd = 8795,
    [switch]$OpenSystemBrowser,
    [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $skillRoot ".venv\Scripts\tmall-materials.exe"

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    if ($SkipBootstrap) {
        throw "RUNTIME_NOT_READY: managed UI executable is missing."
    }
    & (Join-Path $PSScriptRoot "bootstrap.ps1") -Mirror $Mirror
    if ($LASTEXITCODE -ne 0) {
        throw "RUNTIME_BOOTSTRAP_FAILED: bootstrap did not complete."
    }
}

$arguments = @("ui-start", "--port-start", $PortStart, "--port-end", $PortEnd)
if ($Session) { $arguments += @("--session", $Session) }
if ($RunsRoot) { $arguments += @("--runs-root", $RunsRoot) }
if ($Config) { $arguments += @("--config", $Config) }
if ($OpenSystemBrowser) { $arguments += "--open-system-browser" }

& $executable @arguments
exit $LASTEXITCODE
