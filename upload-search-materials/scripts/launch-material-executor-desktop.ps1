param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$Session,
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$Python = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (-not [System.IO.File]::Exists($Python)) {
    throw "Prepared project Python is missing."
}
if ($Session -notmatch '^\d{8}_\d{6}(?:_\d{2})?$') {
    throw "Session id is invalid."
}

function Quote-NativeArgument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

$ExecutorArguments = @(
    "-m",
    "upload_search_materials.cli",
    "material-executor",
    "--session",
    $Session
)
if ($Config) {
    $ResolvedConfig = [System.IO.Path]::GetFullPath($Config)
    $AllowedConfig = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot "config")
    )
    if (
        -not [System.IO.File]::Exists($ResolvedConfig) -or
        -not $ResolvedConfig.StartsWith(
            $AllowedConfig + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Config path is invalid."
    }
    $ExecutorArguments += @("--config", $ResolvedConfig)
}

$ArgumentText = (
    $ExecutorArguments |
        ForEach-Object { Quote-NativeArgument ([string]$_) }
) -join " "

# Shell.Application delegates execution to the existing Windows shell. This
# gives the executor the same non-elevated desktop token and RaiDrive mappings
# as File Explorer instead of inheriting the web/Codex process token.
$DesktopShell = New-Object -ComObject Shell.Application
$DesktopShell.ShellExecute(
    $Python,
    $ArgumentText,
    $ProjectRoot,
    "open",
    0
)
