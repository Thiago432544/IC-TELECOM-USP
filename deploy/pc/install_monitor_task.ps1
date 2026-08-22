# Registra o monitor como tarefa agendada: inicia no boot e reinicia se cair.
# Rodar como Administrador, de dentro da pasta do repo no PC do SPA.
#
#   .\deploy\pc\install_monitor_task.ps1
#   .\deploy\pc\install_monitor_task.ps1 -Python "C:\ProgramData\anaconda3\python.exe"
#
# Nao inicia a tarefa de proposito: se o monitor manual ainda estiver no ar,
# dois processos brigariam pelo getUpdates do Telegram e pelo mesmo SQLite.
param(
    [string]$Python = "",
    [string]$Repo = ""
)
$ErrorActionPreference = "Stop"

$eu = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $eu.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Rode este script como Administrador."
}

# -- 1. onde -----------------------------------------------------------------
if (-not $Repo) { $Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
if (-not (Test-Path (Join-Path $Repo "monitor\service.py"))) {
    throw "Nao achei monitor\service.py em '$Repo'. Rode de dentro do repo, ou passe -Repo."
}

# config.toml esta no .gitignore: um clone novo nasce SEM ele. Registrar a
# tarefa aqui criaria um monitor que morre em todo start, 999 vezes.
$cfg = Join-Path $Repo "config.toml"
if (-not (Test-Path $cfg)) {
    throw ("Falta '$cfg'. Ele esta no .gitignore, entao um clone novo nasce sem ele. " +
           "Copie o da instalacao antiga - e' onde mora o token do Telegram.")
}

# -- 2. qual python ----------------------------------------------------------
# Herdar do processo que ja roda e' mais confiavel que (Get-Command python):
# o monitor do SPA roda no python do Anaconda, que pode nao estar no PATH.
$rodando = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
             Where-Object { $_.CommandLine -like '*-m monitor*' })
if (-not $Python) {
    if ($rodando.Count -gt 0) {
        $Python = $rodando[0].ExecutablePath
        Write-Host "Python herdado do monitor que ja roda: $Python"
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}
if (-not $Python) { throw "Nao achei um python. Passe -Python 'C:\caminho\python.exe'." }
if (-not (Test-Path $Python)) { throw "Python nao existe: '$Python'." }

# -- 3. provar que ESSE python roda o monitor, antes de registrar ------------
Push-Location $Repo
try {
    $saida = & $Python -c "import monitor.service, monitor.charts, monitor.bot; print('IMPORTS OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "'$Python' nao consegue importar o monitor (falta matplotlib/requests?). Saida: $saida"
    }
    Write-Host "$saida  ($Python)"
    $saida = & $Python -c "from monitor.config import load_config; c = load_config('config.toml'); print('CONFIG OK | telegram', c.telegram.enabled)"
    if ($LASTEXITCODE -ne 0) { throw "config.toml nao carrega: $saida" }
    Write-Host $saida
} finally {
    Pop-Location
}

# -- 4. registrar ------------------------------------------------------------
$action  = New-ScheduledTaskAction -Execute $Python -Argument "-m monitor" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtStartup

# SYSTEM, nao o usuario: registrada sob um usuario interativo, a tarefa
# AtStartup so dispara DEPOIS do logon - o monitor ficaria fora ate alguem
# entrar na maquina, que e' exatamente a pane que a tarefa deveria evitar.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "MonitorCamerasPorto" -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "Tarefa 'MonitorCamerasPorto' registrada - e NAO iniciada, de proposito."
if ($rodando.Count -gt 0) {
    Write-Warning ("Ja ha monitor rodando (PID " + ($rodando.ProcessId -join ", ") + "). " +
                   "Pare antes de iniciar a tarefa: dois monitores disputam o getUpdates " +
                   "do Telegram e o mesmo SQLite.")
}
Write-Host ""
Write-Host "Nesta ordem:"
Write-Host "  1. pare o monitor manual:  Stop-Process -Id <PID acima>"
Write-Host "  2. inicie a tarefa:        Start-ScheduledTask -TaskName MonitorCamerasPorto"
Write-Host "  3. confira a tarefa:       Get-ScheduledTaskInfo -TaskName MonitorCamerasPorto"
Write-Host '  4. confira o processo:     Get-CimInstance Win32_Process -Filter "Name=''python.exe''" | Format-List ProcessId, CreationDate, CommandLine'
Write-Host "  5. no Telegram:            /metricas  e  /status"
Write-Host "  6. prova real:             reinicie o PC e confira SEM fazer logon"
Write-Host ""
Write-Host "Rodando como SYSTEM, o processo nao aparece na sua sessao - confira pelos"
Write-Host "comandos acima, nao pelo Gerenciador de Tarefas da sua conta."
