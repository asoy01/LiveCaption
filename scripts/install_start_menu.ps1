<#
    Puts LiveCaption in the Windows Start menu.

    It creates one shortcut:
        %APPDATA%\Microsoft\Windows\Start Menu\Programs\LiveCaption.lnk
    pointing at StartLiveCaptionTray.vbs in this repository.

    **It opens no window.** LiveCaption goes to the task tray, and you right-click
    the tray icon for the control page, the log, and quit. Run
    StartLiveCaption.bat by hand when you want a console to watch.

    The shortcut is written for the current user only, so no administrator
    rights are needed. After that you can press the Windows key, type
    "livecaption", and hit Enter.

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
# **Starts in the task tray, with no console window.** The entry used to run
# StartLiveCaption.bat, which keeps a terminal open for the whole meeting.
# That terminal is useful while you are debugging, but not on a machine that
# stays powered on. Run the .bat by hand when you want to watch it start.
$target    = Join-Path $repo 'StartLiveCaptionTray.vbs'
$runner    = Join-Path $env:SystemRoot 'System32\wscript.exe'
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
    Write-Host '  StartLiveCaptionTray.vbs not found. Expected it here:'
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
    # Call wscript.exe explicitly. Targeting the .vbs works only while .vbs is
    # still associated with Windows Script Host, and that association is easy
    # to lose (an editor can claim it).
    $shortcut.TargetPath       = $runner
    $shortcut.Arguments        = "//B `"$target`""
    $shortcut.WorkingDirectory = $repo
    $shortcut.Description      = 'Real-time subtitles for meetings. Runs in the task tray.'
    $shortcut.WindowStyle      = 7          # minimised; the .vbs shows no window anyway
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
Write-Host '  No window opens. Look for the LiveCaption icon in the task tray.'
Write-Host '  Right-click it for the control page, the log, and quit.'
Write-Host ''
Write-Host '  To start it: press the Windows key, type "livecaption", press Enter.'
Write-Host '  To pin it: find LiveCaption in the Start menu, right-click, "Pin to Start".'
Write-Host ''
Write-Host '  To watch it start up instead, run StartLiveCaption.bat by hand.'
Write-Host '  That one keeps a console window.'
Write-Host ''
