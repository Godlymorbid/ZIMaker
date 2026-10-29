' ZIMaker launcher: starts the converter (hidden, in WSL) and opens it in its own app window.
' The converter stops by itself once the window is closed and no conversion is running.
Option Explicit
Dim sh, fso, dir, wslDir, url, i, distro, f
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
wslDir = "/mnt/" & LCase(Left(dir, 1)) & Replace(Mid(dir, 3), "\", "/")
' 127.0.0.1, not "localhost": Windows tries IPv6 first for localhost, and after WSL restarts its IPv6 forwarding
' can stay dead for minutes, so reopening after the app had stopped by itself would hang
url = "http://127.0.0.1:8765/"

' The WSL distribution the installer chose (none: the default one)
distro = ""
If fso.FileExists(dir & "\distro.txt") Then
  Set f = fso.OpenTextFile(dir & "\distro.txt", 1)
  If Not f.AtEndOfStream Then distro = Trim(f.ReadLine)
  f.Close
End If
' unquoted: wsl.exe takes quotes as part of the name (names can't contain spaces anyway)
If distro <> "" And InStr(distro, " ") = 0 And InStr(distro, """") = 0 Then distro = "-d " & distro & " " Else distro = ""

If Not IsUp() Then
  sh.Run "wsl.exe " & distro & "-e python3 -I """ & wslDir & "/app/server.py""", 0, False
  For i = 1 To 90
    WScript.Sleep 1000
    If IsUp() Then Exit For
  Next
End If
If Not IsUp() Then
  MsgBox "ZIMaker didn't start." & vbCrLf & vbCrLf & _
         "Run 'Start ZIMaker (with console).cmd' in " & dir & " to see what went wrong.", _
         vbExclamation, "ZIMaker"
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
