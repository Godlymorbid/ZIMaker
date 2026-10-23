' ZIM Creator launcher: starts the converter (hidden, in WSL) and opens it in its own app window.
' The converter stops by itself once the window is closed and no conversion is running.
Option Explicit
Dim sh, fso, dir, wslDir, url, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
wslDir = "/mnt/" & LCase(Left(dir, 1)) & Replace(Mid(dir, 3), "\", "/")
url = "http://localhost:8765/"

If Not IsUp() Then
  sh.Run "wsl.exe -e python3 -I """ & wslDir & "/app/server.py""", 0, False
  For i = 1 To 90
    WScript.Sleep 1000
    If IsUp() Then Exit For
  Next
End If
If Not IsUp() Then
  MsgBox "ZIM Creator didn't start." & vbCrLf & vbCrLf & _
         "Run 'Start ZIM Creator (with console).cmd' in " & dir & " to see what went wrong.", _
         vbExclamation, "ZIM Creator"
  WScript.Quit 1
End If

On Error Resume Next
sh.Run "msedge.exe --app=" & url & " --window-size=1000,950", 1, False
If Err.Number <> 0 Then
  Err.Clear
  sh.Run url, 1, False   ' no Edge: fall back to the default browser
End If

Function IsUp()
  On Error Resume Next
  Dim h
  Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  h.setTimeouts 1500, 1500, 1500, 1500
  h.Open "GET", url & "api/ping", False
  h.Send
  IsUp = (Err.Number = 0 And h.Status = 200)
  Err.Clear
End Function
