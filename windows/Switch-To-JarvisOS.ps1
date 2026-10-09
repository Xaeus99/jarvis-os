# J.A.R.V.I.S. (Windows) -> boot JARVIS OS once. Windows stays the default; nothing on C: changes.
# Called by JARVIS's "switch to JARVIS OS" command through the elevated scheduled task (see Install-SwitchTask.ps1),
# so no UAC prompt appears mid-command. Requires an English bcdedit output for the description match.
param([switch]$DryRun)
$ErrorActionPreference = "Stop"
$out = bcdedit /enum firmware
$blocks = ($out -join "`n") -split "`n\s*`n"
$target = $null
foreach ($b in $blocks) {
  $id = [regex]::Match($b, '(?m)^identifier\s+(\{[^}]+\})').Groups[1].Value
  $desc = [regex]::Match($b, '(?m)^description\s+(.+)$').Groups[1].Value.Trim()
  if ($id -and $id -notin @('{fwbootmgr}','{bootmgr}') -and $desc -match 'JARVIS|bazzite|Fedora') { $target = $id; break }
}
if (-not $target) { Write-Error "No JARVIS OS entry in the firmware boot list"; exit 2 }
if ($DryRun) { "would run: bcdedit /set {fwbootmgr} bootsequence $target ; shutdown /r /t 5"; exit 0 }
bcdedit /set '{fwbootmgr}' bootsequence $target | Out-Null
shutdown /r /t 5 /c "J.A.R.V.I.S.: switching to JARVIS OS"
