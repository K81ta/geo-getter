Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(appDir, "GEOGetter.ps1")
If Not fso.FileExists(ps1) Then
  MsgBox "GEOGetter.ps1 was not found: " & ps1, vbCritical, "GEOGetter"
  WScript.Quit 1
End If
cmd = "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Quote(ps1)
shell.Run cmd, 0, False

Function Quote(value)
  Quote = Chr(34) & value & Chr(34)
End Function
