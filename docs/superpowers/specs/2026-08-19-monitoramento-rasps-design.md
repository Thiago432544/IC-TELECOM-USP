# Monitoramento das Raspberry Pis — Projeto Câmeras Porto de Santos

**Data:** 2026-08-19
**Status:** aprovado em conversa; aguardando revisão final do spec
**Origem:** reunião FMEA de 06/07/2026 (riscos R1–R8) + diagnóstico de campo de 18–19/08/2026

---

## 1. Problema

O sistema de câmeras do Porto de Santos não tem monitoramento. As falhas são
descobertas quando alguém repara que as imagens pararam de chegar — às vezes
dias depois. O histórico de pastas em `D:\SPA_Data\Imagens_Porto` registra as
consequências: buracos de set→dez/2025, 55 dias em abr–mai/2026, 27/jul→05/ago
e 06→18/ago.

O diagnóstico de campo (Fase 0, 18–19/08) mostrou que o sistema aparenta saúde
que não tem:

| # | Achado | Evidência |
|---|--------|-----------|
| F1 | A câmera 106 caiu e reconectou **588 vezes em um dia** (pior à tarde/noite: 76 quedas entre 23h–0h) | `~/Desktop/logs_failure/` na 106; inode do diretório com 2,8 MB indica dezenas de milhares de falhas históricas |
| F2 | Causa imediata das quedas: timeout de 1 s no servidor (`rasp_101.py`, `INTERVAL = 1`) mata conexões vivas mas lentas (Δt real da 106: 2,2–3,5 s) → `Broken pipe` na câmera → 10 s fora + reconexão | Console do servidor + terminal da 106 |
| F3 | Relógios errados: 102 está **−40 dias**, 106 **−24 dias**. RTC DS1307 morto nas duas (`probe failed -5`, provável bateria); NTP inativo nas três. A 105 tem RTC funcionando e hora certa | `timedatectl` nos diagnósticos |
| F4 | A 106 tem **subtensão real**: `throttled=0x50000` + 4× `Undervoltage detected!` no dmesg. Fonte ou cabo ruim — causa clássica de corrupção de SD e travamento | dmesg da 106 |
| F5 | **Nenhuma Rasp tem autostart.** Clientes rodam de sessões manuais (105: `sudo python` em pts/0). Queda de energia → câmera morta até alguém logar por VNC | `crontab -l`, autostart, `ps aux` |
| F6 | Cada câmera roda uma versão diferente do cliente: 102/106 → `2026_02_05_Cliente_H01.py`; 105 → `2025_12_22_Cliente_sem_grav_local.py` | `ps aux` |
| F7 | `watchdog_test.py::restart_router()` reinicia o **CPE** via Selenium+Chromium a cada falha de conexão; se o Chrome falhar ao abrir, `driver.quit()` no `except` estoura `NameError` não tratado, que **mata o cliente inteiro** (e sem autostart, a câmera fica morta) | leitura do código |
| F8 | O enlace LTE é **banda de 5 MHz** (700 MHz, ELSYS Amplimax): uplink realista da célula ~6–10 Mbps compartilhado. As 3 câmeras a 1 fps/q90 pedem ~3,6 Mbps sustentados; entrega observada ~1,7 Mbps (102: 100%, 106: 39%, 105: 10%) | print do web UI do CPE do SPA + Δt observados |
| F9 | O disco `D:` do PC enche em ~90 dias (725 GB usados, 275 GB livres, ~3 GB/dia) | `Get-PSDrive` |
| F10 | O web UI do Amplimax expõe RSRP/RSRQ/estado da conexão — coletável por HTTP | print da página de status |

Riscos FMEA cobertos por este projeto: **R1** (Rasp como ponto único de falha —
detecção precoce), **R2** (conhecimento não documentado — configuração
registrada em repositório), **R3** (elo de rádio instável — medição contínua),
**R8** (diagrama desatualizado — inventário real coletado).

## 2. Objetivo

1. **Avisar** — bot no Telegram alerta quando uma câmera/Rasp degrada ou cai,
   **com gráfico do que aconteceu antes** (temperatura, tensão, taxa de
   entrega), não só "caiu".
2. **Diagnosticar** — caixa-preta local em cada Rasp que sobrevive ao
   travamento e ao reboot, para fechar causa raiz (em aberto desde 06/07).
3. **Ver** — painel local no PC com o estado das câmeras, do enlace e do disco.
4. **Não derrubar nada** — o monitoramento nunca pode ser causa de falha do
   pipeline de produção.

**Fora de escopo:** monitoramento direto da eNodeB (endereço real desconhecido;
`192.168.10.100` não responde de nenhum ponto e a máscara /30 do CPE sugere que
esse IP é endereço de rede, não de host — fica para investigação futura);
Rasp da COW/eNodeB (sem acesso de diagnóstico ainda; entra quando houver);
mudanças de qualidade JPEG/fps para caber no enlace (decisão de produto, fora
deste projeto — mas o monitoramento produzirá os dados para embasá-la).

## 3. Topologia real (confirmada na Fase 0)

```
CARRETINHA/POSTES                     8 km LTE 700MHz/5MHz                SPA
Rasp 102 (192.168.11.102, gw .254) ── CPE ELSYS ⟩⟩                ⟩⟩ CPE ELSYS "servidor"
Rasp 105 (192.168.11.105, gw .1)   ── CPE ELSYS ⟩⟩    eNodeB      ⟩⟩ (WAN 192.168.10.101/30)
Rasp 106 (192.168.11.106, gw .254) ── CPE ELSYS ⟩⟩                ⟩⟩       │ encaminha :55000
Rasp COW/eNodeB (ponte, Starlink)                                    PC Windows (CILIP)
                                                                     192.168.11.101 (câmeras)
                                                                     192.168.124.29 (internet)
```

- Clientes discam para `192.168.10.101:55000` (WAN do CPE do SPA), que
  encaminha para o PC `192.168.11.101:55000`.
- Cada Rasp enxerga apenas seu próprio CPE; não há visibilidade lateral.
- O PC tem internet pela segunda interface (192.168.124.29) — é por ela que o
  bot do Telegram sai.
- Novos encaminhamentos de porta no CPE do SPA são configuráveis por nós
  (acesso ao web UI confirmado).

## 4. Arquitetura

```
Rasp 102/105/106 (cada uma)
  camera-client.service   cliente de câmera atual, sob systemd (Restart=always)
  blackbox.service        agente caixa-preta (Fase 3): anel local + push ao PC

PC Windows (coletor) — um único serviço Python ("monitor")
  ├── watcher    D:\SPA_Data por evento → taxa de entrega por câmera
  ├── taplog     segue LOG_connections.txt → eventos CONNECT/DISCONNECT
  ├── cpe        raspa status HTTP do Amplimax (RSRP/RSRQ/estado) a cada 60 s
  ├── ingest     HTTP :55001 recebe telemetria das Rasps (Fase 3)
  ├── store      SQLite (WAL) — série temporal + eventos
  ├── alerta     regras sobre a store → Telegram (mensagem + gráfico PNG)
  └── painel     HTTP :8080 local — estado atual + sparklines 24 h
```

Princípios:

- **Push, nunca pull.** As Rasps estão atrás de NAT; toda telemetria sai delas
  em direção ao PC. O PC nunca precisa alcançar as Rasps.
- **Observação passiva primeiro.** O watcher e o taplog extraem sinal do que o
  pipeline já produz (arquivos e log), com zero mudança no código de produção.
- **O monitoramento não pode causar a falha.** Agente na Rasp: processo único
  e longevo, sem `fork` por amostra (lê `/sys` e `/proc` direto),
  `OOMScoreAdjust=-1000`, anel de tamanho fixo pré-alocado.
- **Relógio confiável é pré-requisito.** Nenhuma correlação entre Rasps e PC
  funciona com −40 dias de desvio (F3).

## 5. Fases

### Fase 1 — Estancar (consertos no que existe)

| # | Mudança | Onde | Detalhe |
|---|---------|------|---------|
| 1.1 | Timeout do servidor 1 s → 30 s | `rasp_101.py` no PC | `INTERVAL = 30` para todos os clientes (o valor 15 da 105 também sobe). Elimina F1/F2. O timeout continua existindo para liberar conexões realmente mortas |
| 1.2 | Autostart via systemd | Rasps 102, 105, 106 | Unit `camera-client.service`: `Restart=always`, `RestartSec=10`, `After=network-online.target`, log para journal. A 105 passa a rodar a **mesma versão** do cliente das outras (H01), eliminando F6 — validar com o time antes, pois a variante "sem_grav_local" pode ser intencional |
| 1.3 | Blindar `restart_router()` | `watchdog_test.py` nas Rasps | `driver = None` antes do try; `if driver: driver.quit()` no except/finally. O NameError de F7 deixa de existir. (Substituir Selenium por HTTP fica para depois — mudança mínima agora) |
| 1.4 | NTP na rede interna | PC + CPE SPA + Rasps | serviço nativo `w32time` do Windows configurado como servidor NTP no PC; encaminhar UDP 123 no CPE do SPA → PC; `systemd-timesyncd` nas Rasps apontando para `192.168.10.101`. Corrige F3 por software. Bateria/módulo RTC da 102 e 106: item de campo na próxima descida |
| 1.5 | Fonte da 106 | campo | Trocar fonte/cabo (F4). Item de campo; o software apenas monitora `get_throttled` para confirmar que sumiu |

Critério de aceite da fase: 24 h sem nenhum arquivo novo em `logs_failure/` da
106 em condições de rádio normais; `timedatectl` sincronizado nas três; kill
manual do cliente em uma Rasp → processo volta em ≤15 s.

### Fase 2 — Ver e avisar (coletor + bot, zero toque nas Rasps)

**Serviço `monitor` no PC** (Python 3.12, dependências: `python-telegram-bot`
ou chamadas HTTP diretas à API do Telegram, `matplotlib` para os gráficos,
`watchdog` para eventos de filesystem; SQLite da stdlib). Roda como tarefa
agendada do Windows (na inicialização, reinício automático em caso de falha).

Métricas coletadas:

| Fonte | Métrica | Período |
|-------|---------|---------|
| watcher em `D:\SPA_Data\Imagens_Porto\<hoje>\<id>` | frames/min por câmera (corrigido pelo `save_every`: 102 e 106 gravam 1/10) | evento |
| taplog em `LOG_connections.txt` | CONNECT/DISCONNECT por câmera, com motivo | evento |
| cpe (HTTP no web UI do Amplimax do SPA) | RSRP, RSRQ, estado da conexão, tecnologia | 60 s |
| local | espaço livre no `D:`, idade do arquivo mais recente por câmera | 60 s |

Armazenamento: SQLite em WAL, uma tabela de amostras (`ts, origem, metrica,
valor`) e uma de eventos (`ts, origem, tipo, detalhe`). Retenção: bruto 90
dias; agregado por hora, indefinido.

**Linha de base e alertas.** Cada câmera tem linha de base própria = mediana
de frames/min por hora do dia sobre os últimos 7 dias (o F1 mostrou padrão
horário forte; a linha de base precisa ser por hora). Regras:

| Alerta | Condição | Ação |
|--------|----------|------|
| Câmera caiu | 0 frames por 3 min | Telegram: mensagem + gráfico 30 min (frames/min, RSRP, eventos de conexão) |
| Câmera degradada | frames/min < 50% da linha de base da hora, por 10 min | idem |
| Flapping | ≥5 DISCONNECT em 15 min | Telegram com contagem e motivos |
| Enlace | RSRP < −110 dBm ou RSRQ < −15 dB por 5 min | Telegram |
| Disco | `D:` livre < 60 GB (≈20 dias) | Telegram diário até resolver |
| Câmera voltou | frames após alerta de queda | Telegram (fecha o incidente) |
| Resumo diário | 08:00 | quedas por câmera, disponibilidade %, tendência de disco, pior hora |

Anti-ruído: um alerta por câmera por condição; re-alerta só após 30 min ou
mudança de estado; janela de silêncio configurável.

**Bot Telegram** (token e chat_id em `config.toml`, fora do git): além dos
alertas, responde `/status` (estado atual das 3 câmeras + enlace + disco) e
`/grafico <id>` (últimas 24 h da câmera).

**Painel** `http://localhost:8080`: cartões por câmera (estado, frames/min,
última imagem recebida, quedas 24 h), sparklines de 24 h, estado do enlace e
do disco. Somente leitura, rede local.

Critério de aceite: derrubar o cliente de uma câmera manualmente → alerta com
gráfico chega no Telegram em ≤4 min e o painel reflete; resumo diário chega às
08:00; watcher e taplog não alteram nenhum arquivo do pipeline (somente
leitura).

### Fase 3 — Caixa-preta nas Rasps

**Agente `blackbox`** (Python stdlib apenas, systemd, `OOMScoreAdjust=-1000`,
`Nice=-10`): a cada 20 s lê — sem criar processos —
`/sys/class/thermal/thermal_zone0/temp`, `/sys/devices/platform/soc/*.firmware/get_throttled`
(fallback: `vcgencmd` só se o sysfs não existir), `/proc/meminfo`,
`/proc/loadavg`, `/proc/stat` (iowait), contagem de processos em estado D
(`/proc/*/stat`), espaço em `/`, flag de filesystem read-only, RSS do processo
do cliente de câmera, e `/proc/pressure/*` quando disponível.

- **Anel local:** arquivo binário de tamanho fixo (~50 MB) em disco,
  pré-alocado, com fsync periódico — sobrevive a travamento e reboot. Após
  boot, o agente publica automaticamente os últimos 30 min pré-reboot.
- **Push:** HTTP POST para `192.168.10.101:55001` (novo encaminhamento no CPE
  do SPA → PC) a cada 20 s, com backlog local quando o link cai (reenvia ao
  voltar). A ausência de push por >90 s é, em si, o heartbeat perdido que o
  coletor alerta.
- **Watchdog de hardware** (`/dev/watchdog`, timeout 60 s): o próprio agente
  afaga o cão somente se um auto-teste passar — consegue `fork`, consegue
  escrever/fsync em disco, `sshd` responde em localhost. Reprovou por 3 ciclos
  → deixa de afagar → reboot automático com motivo gravado no anel.
  Ativação gradual: primeiro na 106, uma semana em observação, depois nas demais.
- `psi=1` no `cmdline.txt` (habilita `/proc/pressure`; requer um reboot por Rasp).
- Quando houver acesso à Rasp da COW/eNodeB, ela recebe o mesmo agente — é a
  máquina do travamento de julho e a principal beneficiária da caixa-preta.

Critério de aceite: simular starvation (consumir RAM com stress) → agente
continua amostrando e o anel registra a degradação; puxar o cabo de rede →
backlog acumula e reenvia ao reconectar; travar o userspace de teste → watchdog
reinicia e o anel mostra os 30 min anteriores.

## 6. Tratamento de erros

- **Coletor cai** → tarefa agendada do Windows reinicia; SQLite em WAL tolera
  interrupção; watcher revarre o diretório do dia ao subir (não perde estado).
- **Telegram inacessível** (internet do PC caiu) → alertas enfileirados em
  disco e enviados ao voltar, com carimbo original; painel local continua.
- **CPE não responde ao scrape** → registra como métrica (`cpe_up=0`); é sinal,
  não erro.
- **Rasp não alcança :55001** → backlog local; sem exceção não tratada.
- **Anel corrompido** (queda de energia no meio de escrita) → recomeça anel
  novo e registra o fato; nunca impede o agente de subir.
- **Duas quedas simultâneas** (ex.: enlace inteiro) → alertas agrupados numa
  mensagem só ("3 câmeras sem frames desde HH:MM — provável enlace/eNodeB").

## 7. Testes

- **Unidade:** parser do `LOG_connections.txt` (formatos reais, linhas
  truncadas); cálculo de linha de base por hora; máquina de estados de alerta
  (histerese, re-alerta, agrupamento); leitura dos formatos de `/proc//sys`
  com fixtures capturadas das Rasps reais.
- **Integração (no PC, sem Rasp):** replay de um dia real de
  `LOG_connections.txt` + mtimes sintéticos → alertas esperados; derrubar e
  religar o cliente de teste → ciclo alerta/recuperação completo.
- **Campo (por fase):** os critérios de aceite de cada fase, acima.
- **Guard-rail permanente:** o coletor abre tudo do pipeline em somente
  leitura; nenhum teste escreve em `D:\SPA_Data`.

## 8. Configuração e segredos

- `config.toml` no PC: token do bot, chat_id, IP/credencial do CPE, limiares.
  Fora do git (`.gitignore`); um `config.example.toml` versionado.
- Repositório: este projeto (`IC-TELECOM-USP`) passa a versionar o código do
  monitor, dos agentes, as units systemd e os scripts de instalação — que é
  também a resposta ao R2: a configuração das Rasps descrita em código, não
  na cabeça de ninguém.

## 9. Pendências assumidas

| Pendência | Tratamento |
|-----------|------------|
| Diagnóstico da Rasp COW/eNodeB | Rodar o script de diagnóstico quando houver acesso; agente na Fase 3 |
| Endereço/protocolo reais da eNodeB | Mapear (traceroute/varredura leve a partir de uma Rasp) em janela combinada; fora do caminho crítico |
| Conteúdo do `LOG_connections.txt` histórico | Usar na calibração da linha de base assim que copiado do PC |
| A variante `sem_grav_local` da 105 é intencional? | Perguntar ao time antes do item 1.2 |
| Quem entra no grupo do Telegram | Decisão do Thiago (sugerido: Daniel, Léo, Hugo) |
| Saturação do enlace (q90/1 fps não cabe em 5 MHz) | Fora de escopo; relatório com dados do monitor subsidia a decisão do time |
