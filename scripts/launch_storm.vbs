' launch_storm.vbs — runs launch_storm.bat with no visible console window.
Dim oShell, scriptDir
Set oShell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
oShell.Run "cmd /c """ & scriptDir & "launch_storm.bat""", 0, False
