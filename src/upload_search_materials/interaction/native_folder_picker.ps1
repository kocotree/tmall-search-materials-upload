param(
    [Parameter(Mandatory = $true)]
    [string]$RequestPath,
    [Parameter(Mandatory = $true)]
    [string]$ResultPath,
    [Parameter(Mandatory = $true)]
    [string]$StatePath
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Result([hashtable]$Value) {
    $Value.request_id = [string]$request.request_id
    $Value.ownership_token = [string]$request.ownership_token
    $Value.helper_pid = $PID
    $json = $Value | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($ResultPath, $json, $utf8)
}

try {
    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = [string]$request.title
    $dialog.ShowNewFolderButton = $false
    if (
        $request.initial_path -and
        [System.IO.Directory]::Exists([string]$request.initial_path)
    ) {
        $dialog.SelectedPath = [string]$request.initial_path
    }
    $owner = New-Object System.Windows.Forms.Form
    $owner.ShowInTaskbar = $false
    $owner.StartPosition = "CenterScreen"
    $owner.TopMost = $true
    $owner.Opacity = 0
    $owner.Show()
    $owner.Activate()
    $state = @{
        schema_version = 1
        status = "window_visible"
        request_id = [string]$request.request_id
        ownership_token = [string]$request.ownership_token
        helper_pid = $PID
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($StatePath, $state, $utf8)
    $result = $dialog.ShowDialog($owner)
    $owner.Close()
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
        detail = [string]$_.Exception.Message
        path = ""
    }
}
