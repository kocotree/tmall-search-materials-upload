param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$Session,
    [string]$UserDataRoot = "",
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$RuntimeRoot = if ($env:TMALL_RUNTIME_ROOT) {
    [System.IO.Path]::GetFullPath($env:TMALL_RUNTIME_ROOT)
}
elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "tmall-search-materials\runtime"
}
else {
    Join-Path $HOME ".local\state\tmall-search-materials\runtime"
}
$Python = Join-Path $RuntimeRoot ".venv\Scripts\pythonw.exe"
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
    (Join-Path $ProjectRoot "scripts\run-plugin.py"),
    "material-executor",
    "--session",
    $Session
)
if ($Config) {
    $ResolvedConfig = [System.IO.Path]::GetFullPath($Config)
    $AllowedConfig = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot "config")
    )
    $ResolvedUserDataRoot = if ($UserDataRoot) {
        [System.IO.Path]::GetFullPath($UserDataRoot)
    }
    elseif ($env:TMALL_USER_DATA_ROOT) {
        [System.IO.Path]::GetFullPath($env:TMALL_USER_DATA_ROOT)
    }
    else {
        [System.IO.Path]::GetFullPath((Split-Path -Parent $RuntimeRoot))
    }
    $AllowedUserConfig = [System.IO.Path]::GetFullPath(
        (Join-Path $ResolvedUserDataRoot "config")
    )
    if (
        -not [System.IO.File]::Exists($ResolvedConfig) -or
        -not (
            $ResolvedConfig.StartsWith(
                $AllowedConfig + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            $ResolvedConfig.StartsWith(
                $AllowedUserConfig + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase
            )
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
