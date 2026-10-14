param()
$ErrorActionPreference='Stop'
$taskGamePath=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'local-client/Bin/Game/Game.exe'))
$taskGameProcesses=@(Get-CimInstance Win32_Process -Filter "Name='Game.exe'" | Where-Object { $_.ExecutablePath -and [string]::Equals($_.ExecutablePath,$taskGamePath,[StringComparison]::OrdinalIgnoreCase) })
foreach ($taskGameProcess in $taskGameProcesses) { Stop-Process -Id $taskGameProcess.ProcessId }
Write-Output "Cliente privado fechado: $($taskGameProcesses.Count) processo(s)."
