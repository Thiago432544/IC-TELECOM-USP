# Registra o SERVIDOR das cameras como tarefa agendada: sobe no boot do PC,
# sem ninguem fazer logon, e volta sozinho se cair. Rodar como Administrador.
#
#   .\deploy\pc\install_server_task.ps1
#   .\deploy\pc\install_server_task.ps1 -Script "C:\Users\CILIP\Documents\2026_02_01_Server_H00.py"
#
# Por que: em 29/10/2026 as 14:58 o PC do SPA reiniciou (provavel atualizacao
# do Windows). O monitor, que ja e' tarefa agendada, voltou sozinho; o
# servidor, que rodava numa janela aberta a mao, nao. Ficou desligado ate
# 30/10 07:27 e nenhuma camera conseguiu entrar nesse tempo.
#
# Nao inicia a tarefa de proposito: com o servidor manual ainda no ar, dois
# processos disputariam a porta 55000 (o segundo morre com "address in use").
param(
    [string]$Script = "C:\Users\CILIP\Documents\2026_02_01_Server_H00.py",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$eu = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $eu.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Rode este script como Administrador."
}

# -- 1. qual servidor ---------------------------------------------------------
# O nome do arquivo engana (em 19/08 o patch foi aplicado a um arquivo com
# outro nome). Mostra o que esta rodando agora para comparar com -Script.
if (-not (Test-Path $Script)) { throw "Servidor nao existe: '$Script'." }
$rodando = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
             Where-Object { $_.CommandLine -like '*Server*' })
foreach ($p in $rodando) {
    Write-Host ("Servidor rodando agora (PID " + $p.ProcessId + "): " + $p.CommandLine)
}
if ($rodando.Count -gt 0 -and -not ($rodando | Where-Object { $_.CommandLine -like "*$Script*" })) {
    Write-Warning "O servidor que esta rodando NAO e' '$Script'. Confira qual e' o certo antes de seguir."
}

# -- 2. qual python -------------------------------------------------------------
# Herda do servidor que ja roda (o do Anaconda, fora do PATH).
if (-not $Python) {
    if ($rodando.Count -gt 0) {
        $Python = $rodando[0].ExecutablePath
        Write-Host "Python herdado do servidor que ja roda: $Python"
    } elseif (Test-Path "C:\ProgramData\anaconda3\python.exe") {
        $Python = "C:\ProgramData\anaconda3\python.exe"
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}
if (-not $Python) { throw "Nao achei um python. Passe -Python 'C:\caminho\python.exe'." }
if (-not (Test-Path $Python)) { throw "Python nao existe: '$Python'." }

# -- 3. provar que esse python compila o servidor, antes de registrar ----------
$saida = & $Python -m py_compile $Script 2>&1
if ($LASTEXITCODE -ne 0) { throw "'$Python' nao compila '$Script': $saida" }
Write-Host "COMPILA OK ($Python)"
if (-not (Test-Path "D:\SPA_Data")) {
    Write-Warning "D:\SPA_Data nao existe: o servidor grava as imagens e o LOG_connections la."
}

# -- 4. registrar ------------------------------------------------------------------
$pasta = Split-Path -Parent $Script
$action  = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Script + '"') -WorkingDirectory $pasta
$trigger = New-ScheduledTaskTrigger -AtStartup
# SYSTEM: dispara no boot sem esperar logon (o mesmo motivo do monitor).
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "ServidorCamerasPorto" -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "Tarefa 'ServidorCamerasPorto' registrada - e NAO iniciada, de proposito."
Write-Host ""
Write-Host "Nesta ordem:"
Write-Host "  1. pare o servidor manual:  feche a janela dele, ou Stop-Process -Id <PID acima>"
Write-Host "  2. inicie a tarefa:         Start-ScheduledTask -TaskName ServidorCamerasPorto"
Write-Host "  3. confira a porta:         netstat -ano | findstr :55000   (LISTENING)"
Write-Host "  4. confira as cameras:      /status no Telegram, em ate alguns minutos"
Write-Host "  5. prova real:              reinicie o PC e confira SEM fazer logon"
Write-Host ""
Write-Host "Sem janela: as mensagens do servidor nao aparecem mais na tela. O que importa"
Write-Host "continua em D:\SPA_Data\LOG_connections.txt."
