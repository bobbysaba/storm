' launch_storm.vbs — runs launch_storm.bat with no visible console window.
' Uses WScript.Shell with window style 0 (hidden) to completely suppress the console.
Dim oShell, scriptDir
Set oShell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
' Window style 0 = hidden, False = don't wait for completion
oShell.Run "cmd /c """ & scriptDir & "launch_storm.bat""", 0, False
