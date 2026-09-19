' LiveCaption をタスクトレイに常駐させる。**窓を1つも出さない。**
'
' 常時起動の機体で使う。ターミナルを開いたままにしたくないが、窓を消すと
' 生きているのか分からなくなるので、トレイにアイコンを出す。
'
'   ● 緑  会議の字幕を出している
'   ● 青  待機中
'   ● 赤  失敗が残っている
'
' 右クリックで、操作画面・ログ・終了。
' ログは local\log\ に残る（窓が無いので、そこが唯一の手がかりになる）。
'
' **.bat ではなく .vbs にしてあるのは、窓を完全に消すためである。** .bat は
' タスクスケジューラから起動しても、一瞬コンソールが光る。
'
' 手で起動して様子を見たいときは、StartLiveCaption.bat のほうを使うこと。

Option Explicit

Dim shell, fso, here, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = here

' --no-browser にしてある。常駐の起動でブラウザが開くのは邪魔である。
' 操作画面はトレイの右クリックから開く。
cmd = "pixi run caption --web --tray --no-browser --control-bind"

' 0 = 窓を出さない、False = 終わるのを待たない
shell.Run cmd, 0, False
