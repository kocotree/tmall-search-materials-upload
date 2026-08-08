[CmdletBinding()]
param(
    [ValidateSet("register", "update", "status")]
    [string]$Mode = "status",
    [string]$CodexHome = $env:CODEX_HOME
)

$ErrorActionPreference = "Stop"
$skillRoot = (Split-Path -Parent $PSScriptRoot)
if (-not $CodexHome) {
    $CodexHome = Join-Path $env:USERPROFILE ".codex"
}
$skillsRoot = Join-Path $CodexHome "skills"
$target = Join-Path $skillsRoot "upload-search-materials"
$canonical = Join-Path $skillRoot "SKILL.md"

if (-not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
    throw "SKILL_CANONICAL_NOT_FOUND: $canonical"
}

function Get-RegistrationStatus {
    if (-not (Test-Path -LiteralPath $target)) {
        return "not_registered"
    }
    $targetCanonical = Join-Path $target "SKILL.md"
    if (-not (Test-Path -LiteralPath $targetCanonical -PathType Leaf)) {
        return "invalid"
    }
    $sourceSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $canonical).Hash
    $targetSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetCanonical).Hash
    if ($sourceSha -eq $targetSha) {
        return "current"
    }
    return "stale_or_foreign"
}

$status = Get-RegistrationStatus
if ($Mode -eq "status") {
    Write-Output "STATUS=$status"
    Write-Output "TARGET=$target"
    exit 0
}

if ($status -eq "stale_or_foreign" -or $status -eq "invalid") {
    throw "SKILL_REGISTRATION_CONFLICT: '$target' is not the current repository Skill; remove or relocate it explicitly before registering."
}
if ($status -eq "current") {
    Write-Output "STATUS=current"
    Write-Output "TARGET=$target"
    exit 0
}

New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null
$arguments = "/c mklink /J `"$target`" `"$skillRoot`""
$process = Start-Process -FilePath "cmd.exe" -ArgumentList $arguments -Wait -PassThru -NoNewWindow
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $target)) {
    throw "SKILL_REGISTRATION_FAILED: could not create repository-backed junction at '$target'."
}
Write-Output "STATUS=current"
Write-Output "TARGET=$target"
