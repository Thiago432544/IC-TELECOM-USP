# Runbook — Fase 1 em campo

Ordem pensada para nunca deixar o sistema pior do que estava. Cada bloco é
reversível de forma independente.

## A. No PC do SPA (remoto, ~15 min)
1. `git clone` / copiar a pasta `deploy/pc/` para o PC.
2. **Servidor NTP:** PowerShell como admin → `.\enable_ntp_server.ps1`.
   Validar: `w32tm /query /status` mostra fonte e stratum.
3. **Timeout do servidor de imagens:**

   > ⚠️ Em 19/08 este passo foi dado como aplicado e **não estava**: o patch foi
   > escrito no `2026_02_01_Server_H00.py`, que **não é o arquivo de
   > produção**. O servidor rodou mais um dia inteiro com timeout de 1 s.

   - **Descobrir qual arquivo está realmente rodando.** Não confie no nome do
     arquivo nem no título da janela:
     ```powershell
     Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
       Select-Object ProcessId, CreationDate, CommandLine | Format-List
     ```
     Em 20/08/2026: `C:\Users\CILIP\Documents\2026_02_01_Server_H00.py`,
     lançado pelo Python do Anaconda.
   - Conferir que o processo não é mais velho que o arquivo — Python lê o `.py`
     uma única vez, na inicialização. Arquivo editado depois do `CreationDate`
     do processo = patch no disco, código velho na memória.
   - Backup: `Copy-Item $f "$f.bak-<data>"`.
   - **Edição mínima, uma linha.** Não substituir o arquivo inteiro: há várias
     versões em circulação (F6) e não se sabe como a de produção diverge em
     `HOST`, `SAVE_PATH` ou `save_every`.
     `INTERVAL = 15 if client_id == "105" else 1` → `INTERVAL = 30`
   - **Reiniciar parando e subindo no mesmo comando.** Se o servidor ficar fora
     por mais de ~10 s, o `connect()` da câmera falha, o `main()` do cliente
     chama `restart_router()` e o CPE 192.168.11.254 reinicia — derrubando a 102
     e a 106 juntas, com backoff até 1800 s (achado 3.1). Uma queda com o
     servidor no ar é inofensiva: cai no `except` interno do `capture_and_send()`,
     que espera 10 s e reconecta sem tocar no CPE.
     ```powershell
     Stop-Process -Id <pid>; Start-Process "C:\ProgramData\anaconda3\python.exe" -ArgumentList '"C:\Users\CILIP\Documents\2026_02_01_Server_H00.py"'
     ```
   - **Validar de verdade.** A linha `SERVIDOR INICIADO` no `LOG_connections.txt`
     **não prova que o servidor subiu**: ela é gravada *antes* do `srv.bind()`,
     então aparece igual quando a porta já está ocupada e o processo morre em
     seguida. Aconteceu em 20/08 às 20:45:36 — linha no log, servidor nenhum.
     O que prova:
     ```powershell
     netstat -ano | findstr :55000     # precisa ter LISTENING no PID novo
     ```
     mais três linhas `CONNECT` novas no `LOG_connections.txt` em até ~20 s, e o
     `logs_failure` da 106 parando de crescer.

## B. No web UI do CPE ELSYS do SPA (~5 min)
4. Encaminhamento de porta: **UDP 123 → 192.168.11.101** (NTP).
   (Anotar aqui o caminho de menu real do Amplimax ao fazer.)

## C. Em cada Rasp, por VNC — 102, 106, depois 105 (~10 min cada)
5. Copiar `deploy/rasp/` para a Rasp (scp ou pendrive na descida).
6. `sudo bash install_camera_client.sh`
7. Parar o cliente manual (Ctrl+C na sessão VNC/terminal em que roda hoje).
8. `sudo systemctl start camera-client` → conferir no PC que a câmera voltou.
9. `timedatectl` → `System clock synchronized: yes` (pode levar ~1 min).
10. **Na 105 antes dos passos 6–8:** confirmar com o time que ela pode migrar
    da variante `2025_12_22_Cliente_sem_grav_local.py` para a H01 (pendência
    da spec). Se não puder: editar `ExecStart` da unit para apontar a variante.
11. Teste do autostart: `sudo systemctl kill camera-client` → processo volta
    em ≤15 s; `sudo reboot` → câmera volta sozinha ao PC em ~2 min.

## D. Itens físicos (próxima descida a Santos)
12. **Trocar a fonte/cabo da 106** (subtensão real: `throttled=0x50000`).
    Validar depois: `vcgencmd get_throttled` → `0x0` e permanecer.
13. Bateria/módulo RTC (DS1307) da 102 e da 106; validar `timedatectl`
    mostra RTC time.
14. Rodar o diagnóstico padrão na Rasp da COW/eNodeB (pendência da spec).

## Critério de aceite da Fase 1 (spec §5)
- 24 h sem novo arquivo em `logs_failure/` da 106 (rádio normal).
- `timedatectl` sincronizado nas três Rasps.
- Kill do cliente → volta em ≤15 s.
