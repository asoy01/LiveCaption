' LiveCaption - start in the task tray, with no window at all.
'
' Use this on a machine that stays powered on. It keeps no terminal open,
' so the tray icon is how you see whether it is alive:
'
'   blue   waiting
'   green  captions are running
'   red    a failure is waiting to be read
'
' Right-click the icon for the control page, the log, and quit.
' The log is written to local\log\ (there is no window to read).
'
' To watch it start up, use StartLiveCaption.bat instead. That one keeps a
' console window.
'
'
' ===  THIS FILE MUST STAY PURE ASCII  ========================================
'
' Windows Script Host reads .vbs as the system ANSI code page, NOT as UTF-8.
' A .vbs saved as UTF-8 with Japanese comments cannot be decoded, and WSH then
' does nothing at all - no error, no window, no process. That is exactly what
' happened on 2026-09-19: double-clicking this file appeared to do nothing.
'
' So: no Japanese, no accented letters, nothing above 0x7F in this file.
' The Japanese explanation lives in docs/manual.ja.md, section 4.5.
' =============================================================================

Option Explicit

Dim shell, fso, here, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = here

' --no-browser: a resident start should not pop a browser open.
' Open the control page from the tray icon instead.
cmd = "pixi run caption --web --tray --no-browser --control-bind"

' 0 = no window, False = do not wait for it to finish
shell.Run cmd, 0, False
