[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = $skillRoot
$canonicalPath = Join-Path $skillRoot "SKILL.md"
$entryPath = Join-Path $repositoryRoot ".codex\skills\upload-search-materials\SKILL.md"

if (-not (Test-Path -LiteralPath $canonicalPath -PathType Leaf)) {
    throw "SKILL_CANONICAL_NOT_FOUND: $canonicalPath"
}
if (-not (Test-Path -LiteralPath $entryPath -PathType Leaf)) {
    throw "SKILL_DISCOVERY_ENTRY_NOT_FOUND: $entryPath"
}

$sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $canonicalPath).Hash.ToLowerInvariant()
$text = Get-Content -LiteralPath $entryPath -Raw -Encoding UTF8
$updated = [regex]::Replace(
    $text,
    '(?m)(Generated from canonical SHA-256:\r?\n`)[0-9a-f]{64}(`)',
    {
        param($match)
        $match.Groups[1].Value + $sha + $match.Groups[2].Value
    }
)
if ($updated -eq $text -and $text -notmatch [regex]::Escape($sha)) {
    throw "SKILL_ENTRY_UPDATE_FAILED: source SHA marker is missing"
}
[System.IO.File]::WriteAllText($entryPath, $updated, [System.Text.UTF8Encoding]::new($false))
Write-Output "UPDATED_SHA256=$sha"
