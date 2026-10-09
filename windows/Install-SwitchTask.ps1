# One-time setup (run as admin, from the JARVIS v5.x installer): registers a task that runs the switch script
# with highest privileges, so the voice command can start it without a UAC prompt. Remove: Unregister-ScheduledTask JarvisSwitchToJarvisOS
$script = Join-Path $PSScriptRoot "Switch-To-JarvisOS.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
Register-ScheduledTask -TaskName "JarvisSwitchToJarvisOS" -Action $action -Principal $principal -Force | Out-Null
"Registered. JARVIS triggers it with: schtasks /run /tn JarvisSwitchToJarvisOS"
