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

' The browser chosen in Settings (written by ZIMaker next to this file). Chromium browsers open ZIMaker in its own
' app window; Firefox has no app mode, so it gets a new window. If the chosen one isn't installed: Edge, then the
' default browser.
Dim browser, app
browser = "auto"
If fso.FileExists(dir & "\browser.txt") Then
  Set f = fso.OpenTextFile(dir & "\browser.txt", 1)
  If Not f.AtEndOfStream Then browser = LCase(Trim(f.ReadLine))
  f.Close
End If
app = " --app=" & url & " --window-size=1000,950"
Select Case browser
  Case "chrome":  If Not Launch("chrome.exe" & app) Then FallBack
  Case "brave":   If Not Launch("brave.exe" & app) Then FallBack
  Case "firefox": If Not Launch("firefox.exe -new-window " & url) Then FallBack
  Case "default": If Not Launch(url) Then FallBack
  Case Else:      FallBack   ' auto / edge
End Select

Sub FallBack()
  If Not Launch("msedge.exe" & app) Then Launch url   ' no Edge: the default browser
End Sub

Function Launch(cmd)
  On Error Resume Next
  sh.Run cmd, 1, False
  Launch = (Err.Number = 0)
  Err.Clear
End Function

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
