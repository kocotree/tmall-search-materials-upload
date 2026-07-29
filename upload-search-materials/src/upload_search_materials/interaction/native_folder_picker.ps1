param(
    [Parameter(Mandatory = $true)]
    [string]$RequestPath,
    [Parameter(Mandatory = $true)]
    [string]$ResultPath
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Result([hashtable]$Value) {
    $json = $Value | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($ResultPath, $json, $utf8)
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = [string]$request.title
    $dialog.ShowNewFolderButton = $false
    if (
        $request.initial_path -and
        [System.IO.Directory]::Exists([string]$request.initial_path)
    ) {
        $dialog.SelectedPath = [string]$request.initial_path
    }
    $result = $dialog.ShowDialog()
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Result @{
            schema_version = 1
            status = "selected"
            path = [string]$dialog.SelectedPath
        }
    }
    else {
        Write-Result @{
            schema_version = 1
            status = "cancelled"
            path = ""
        }
    }
}
catch {
    Write-Result @{
        schema_version = 1
        status = "error"
        reason_code = "FOLDER_PICKER_GUI_UNAVAILABLE"
        path = ""
    }
}
