<#
    Make LiveCaption start automatically at logon.

    It creates a task named "LiveCaption" in Task Scheduler. What it starts is
    StartLiveCaptionTray.vbs, and no window opens at all. The task tray icon
    shows you the state.

        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove

    No administrator rights are needed. It creates one task, for this user
    only.

    **Do not choose "Run whether user is logged on or not".** That runs the
    task in session 0. Session 0 has no desktop, so the Zoom window does not
    appear and the audio does not connect. The VB-CABLE path is also per
    session. So this task is "At log on" and "Run only when user is logged on".

    **Leave the machine logged on.** A locked screen is fine. If you log off,
    this task cannot run. After a reboot, the caption program does not come up
    until somebody logs on (whether to turn on automatic logon is a decision
    for whoever runs the machine).

    Run it again after you move or rename the repository. It remembers an
    absolute path.
#>

[CmdletBinding()]
param(
    # Delete the task.
    [switch]$Remove,
    # The name of the task. The default is LiveCaption.
    [string]$TaskName = 'LiveCaption'
)

$ErrorActionPreference = 'Stop'

$repo   = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repo 'StartLiveCaptionTray.vbs'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host ''
        Write-Host "  タスクを消した: $TaskName"
        Write-Host ''
    } else {
        Write-Host ''
        Write-Host "  タスクは無い: $TaskName"
        Write-Host ''
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $target)) {
    throw "起動するファイルが無い: $target"
}

# Hand the .vbs to wscript.exe. //B also keeps dialogs from appearing.
$action = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument "//B `"$target`"" -WorkingDirectory $repo

# **At log on.** Nothing else, so that session 0 is avoided.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Run it as the interactive user (it needs a desktop).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

# **Do not wait for Tailscale.** The program retries in the background until
# it gets the tailnet address. Waiting here would only delay the start of the
# caption program itself.
$trigger.Delay = 'PT20S'

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ''
Write-Host "  登録した: $TaskName"
Write-Host "    起動するもの: $target"
Write-Host '    ログオンの20秒後に起動する。窓は出ない。'
Write-Host '    状態はタスクトレイのアイコンで分かる。'
Write-Host ''
Write-Host '  いま試すなら:'
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host ''
Write-Host '  消すなら:'
Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove'
Write-Host ''
Write-Host '  注意: この機体はログオンしたままにしておくこと。画面のロックは構わない。'
Write-Host '        ログオフすると動けない。'
Write-Host ''
