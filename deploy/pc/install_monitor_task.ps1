# Registra o monitor como tarefa agendada: inicia no boot e reinicia se cair.
# Rodar como Administrador na pasta do repo clonado no PC.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = (Get-Command python).Source

$action = New-ScheduledTaskAction -Execute $py -Argument "-m monitor" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 3650) -StartWhenAvailable
Register-ScheduledTask -TaskName "MonitorCamerasPorto" -Action $action `
  -Trigger $trigger -Settings $settings -RunLevel Highest -Force
Start-ScheduledTask -TaskName "MonitorCamerasPorto"
Write-Host "OK. Painel: http://localhost:8080  |  Get-ScheduledTask MonitorCamerasPorto"
