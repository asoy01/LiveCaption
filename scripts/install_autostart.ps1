<#
    LiveCaption を、ログオン時に自動で起動させる。

    タスクスケジューラに「LiveCaption」という名前のタスクを作る。
    起動するのは StartLiveCaptionTray.vbs で、窓は1つも出ない。
    状態はタスクトレイのアイコンで分かる。

        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove

    管理者権限は要らない。作るのは、このユーザーのタスク1つだけである。

    **「ユーザーがログオンしているかどうかにかかわらず実行する」は選ばない。**
    それを選ぶとセッション0で走る。セッション0にはデスクトップが無いので、
    Zoomの画面が出ず、音も繋がらない。VB-CABLE の経路もセッションごとである。
    だからこのタスクは「ログオン時」かつ「ユーザーがログオンしているときのみ」にする。

    **機体はログオンしたままにしておくこと。** 画面がロックされているのは構わない。
    ログオフすると、このタスクは動けない。再起動のあとは、誰かがログオンするまで
    字幕アプリは上がらない（自動ログオンにするかどうかは運用の判断である）。

    リポジトリを移動・改名したら、もう一度実行すること。パスを絶対で覚えている。
#>

[CmdletBinding()]
param(
    # タスクを消す。
    [switch]$Remove,
    # タスクの名前。既定は LiveCaption。
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

# wscript.exe に .vbs を渡す。//B で、途中のダイアログも出さない。
$action = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument "//B `"$target`"" -WorkingDirectory $repo

# **ログオン時。** セッション0を避けるため、これ以外は選ばない。
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# 対話するユーザーとして走らせる（デスクトップが要る）。
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

# **Tailscale を待たない。** アプリ側が、tailnet のアドレスを取れるまで
# 背景で試し直す。ここで待たせると、字幕アプリの起動そのものが遅れる。
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
