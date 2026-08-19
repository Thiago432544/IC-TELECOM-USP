# Runbook — Fase 1 em campo

Ordem pensada para nunca deixar o sistema pior do que estava. Cada bloco é
reversível de forma independente.

## A. No PC do SPA (remoto, ~15 min)
1. `git clone` / copiar a pasta `deploy/pc/` para o PC.
2. **Servidor NTP:** PowerShell como admin → `.\enable_ntp_server.ps1`.
   Validar: `w32tm /query /status` mostra fonte e stratum.
3. **Timeout do servidor de imagens:**
   - Fechar a janela "Servidor_receb_Imagens_Rasp" (as câmeras entram em
     reconexão automática — sem pressa).
   - Backup: copiar o `.py` atual para `rasp_101.py.bak-<data>`.
   - Substituir pelo `deploy/pc/Servidor_receb_Imagens_Rasp.py`.
   - Rodar de novo (mesmo atalho de sempre). Validar: as 3 câmeras
     reconectam (🟢 no console) e `logs_failure` da 106 para de crescer.

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
