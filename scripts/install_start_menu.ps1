<#
    Puts LiveCaption in the Windows Start menu.

    It creates one shortcut:
        %APPDATA%\Microsoft\Windows\Start Menu\Programs\LiveCaption.lnk
    pointing at StartLiveCaption.bat in this repository.

    The shortcut is written for the current user only, so no administrator
    rights are needed. After that you can press the Windows key, type
    "livecaption", and hit Enter to start a meeting.

    Usage (from a terminal):
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_start_menu.ps1
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_start_menu.ps1 -Remove

    Or just double-click InstallToStartMenu.bat in the repository root.

    Re-run it after you move or rename the repository folder. The shortcut
    stores an absolute path, so it stops working when the folder moves.
#>

[CmdletBinding()]
param(
    # Delete the Start menu shortcut instead of creating it.
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$repo      = Split-Path -Parent $PSScriptRoot
$target    = Join-Path $repo 'StartLiveCaption.bat'
$icon      = Join-Path $repo 'etc\LiveCaption.ico'
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$link      = Join-Path $startMenu 'LiveCaption.lnk'

if ($Remove) {
    if (Test-Path -LiteralPath $link) {
        Remove-Item -LiteralPath $link
        Write-Host ''
        Write-Host '  Removed from the Start menu:'
        Write-Host "    $link"
        Write-Host ''
    } else {
        Write-Host ''
        Write-Host '  Not in the Start menu. Nothing to remove.'
        Write-Host ''
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $target)) {
    Write-Host ''
    Write-Host '  StartLiveCaption.bat not found. Expected it here:'
    Write-Host "    $target"
    Write-Host ''
    Write-Host '  Run this script from inside the LiveCaption repository.'
    Write-Host ''
    exit 1
}

if (-not (Test-Path -LiteralPath $startMenu)) {
    New-Item -ItemType Directory -Path $startMenu | Out-Null
}

$existed = Test-Path -LiteralPath $link

$shell = New-Object -ComObject WScript.Shell
try {
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath       = $target
    $shortcut.WorkingDirectory = $repo
    $shortcut.Description      = 'Real-time English subtitles for meetings held in Japanese'
    $shortcut.WindowStyle      = 1          # normal window, so the console stays visible
    if (Test-Path -LiteralPath $icon) {
        $shortcut.IconLocation = "$icon,0"
    }
    $shortcut.Save()
} finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}

Write-Host ''
if ($existed) {
    Write-Host '  Updated the Start menu shortcut:'
} else {
    Write-Host '  Added to the Start menu:'
}
Write-Host "    $link"
Write-Host "  It runs: $target"
Write-Host ''
Write-Host '  To start a meeting: press the Windows key, type "livecaption", press Enter.'
Write-Host '  To pin it: find LiveCaption in the Start menu, right-click, "Pin to Start".'
Write-Host ''
