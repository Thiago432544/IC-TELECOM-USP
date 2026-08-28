# Estudo — saber por que uma câmera caiu

**Estado: estudo, aguarda decisão do time.** Escrito em 25/08/2026 com a 102
fora há mais de um dia (desde 24/08 04:37) e sem explicação além de "o caminho
até ela morreu". Baseado nos diários de 19, 20 e 24/08, nas evidências de
`docs/evidencias/fase0/`, no cliente H01, no servidor de produção e no código
atual do monitor.

## 0. Resumo

O bot hoje sabe **que** uma câmera caiu e **quando**, com precisão de segundo,
e rotula a queda com a última linha `DISCONNECT` do servidor. Não sabe **por
quê** — e não tem como: das dez famílias de falha que este projeto já
encontrou, sete deixam exatamente a mesma marca vista do PC, que é silêncio.

Três coisas mudam isso, em ordem de custo:

1. **Uma sonda do PC** (S1) — TCP por minuto à Pi e ao CPE de cada câmera, ao
   gateway LTE e à porta 55000 local. Zero toque nas Rasps. Separa "servidor"
   de "enlace do SPA", e "Pi viva" de "caminho morto". Ela só é possível porque
   a premissa "o PC não alcança as Rasps" caiu em 24/08.
2. **Diagnóstico automático com linha do tempo no bot** (S2 + S3) — `/queda
   <câmera>` e dentro do próprio alerta. Junta o que o banco já tem (idade,
   taxa, motivo do servidor, simultaneidade, reaberturas, cobertura) com a
   sonda, e escreve um veredito **dizendo o que não consegue separar**.
3. **A caixa-preta da Fase 3, reposicionada** (S4 + S5) — primeiro o que já
   está escrito e não aplicado (autostart, NTP, F7) mais uma linha de watchdog
   do systemd; depois um agente mínimo que manda o que só a Pi sabe, incluindo
   o *tail* dos logs que ela já grava e ninguém lê.

Há um teto: queda de energia no poste e travamento de kernel se parecem em
qualquer registro. A seção 5 diz exatamente onde o teto está.

Para a 102, o teste da seção 3.3 foi rodado em 25/08: **nada no poste dela
responde**, enquanto os outros dois postes respondem. Energia do poste, CPE,
SIM ou rádio. O resto é campo — a seção 3.4 diz o que olhar primeiro.

## 1. O que o bot sabe hoje

Ingredientes já no banco (`monitor.db`):

| Fonte | O que dá | Onde é usado |
|---|---|---|
| `watcher.py` | `frames_min` e `last_frame_age_s` por câmera, a cada 10 s | alerta `down`/`degraded`, `/status`, gráficos |
| `taplog.py` | `CONNECT`/`DISCONNECT` com o motivo do servidor, `SERVER_START` | rótulo da queda, `flapping` |
| `alerts.py` | `alert_*` como evento | histórico |
| `uptime.py` | quedas reconstruídas pela idade do frame; lacunas de cobertura do monitor | faixa de conexão, `/status` |

O rótulo de uma queda é o último `DISCONNECT` dentro dela (`uptime._motivo`).
O alerta de queda é `Camera 102 SEM FRAMES ha 3min.` mais a faixa de conexão
de **24 h** — que, para uma queda de três minutos, é uma linha vazada no canto
direito.

O que o diário de 24/08 reconstruiu à mão, em horas de trabalho, sobre a 102:

| Hora | Fato | Onde estava |
|---|---|---|
| 03:37–04:37 | 57 f/min, estável, igual à base da hora | `samples` |
| 04:37:20 | último frame | `samples` (idade) |
| 04:37:56 | `Timeout, encerrando conexao` — 36 s depois, com timeout de 30 s | `events` |
| 04:37–04:40 | 105 e 106 sem nenhum alerta | `events` |
| 11:12:53, 11:28:04 | `SERVIDOR INICIADO`; 105 e 106 reconectaram em segundos, a 102 não | `events` |
| 24/08 | `192.168.10.102:22` não responde; `.105` e `.106` respondem | fora do banco |

**Tudo, menos a última linha, estava no banco.** O trabalho foi humano porque
não existe código que junte as peças e escreva a frase. É isso que a S2 faz.

## 2. Famílias de falha × o que cada fonte enxerga

A tabela é o centro do estudo. Colunas: o que o PC vê hoje; o que a sonda (S1)
acrescenta; o que só a caixa-preta (S5) vê; e se a câmera volta sozinha depois
da S4.

| # | Família | Hoje (imagens + LOG) | Com a sonda (S1) | Só a caixa-preta (S5) | Volta sozinha (S4)? |
|---|---|---|---|---|---|
| 1 | Servidor parado, congelado (F11) ou reiniciado por alguém | todas caem juntas (`group_down`); `SERVER_START` se reiniciou | porta 55000 local não escuta → **certo** | — | sim (autostart do servidor já existe) |
| 2 | Enlace do SPA / eNodeB | todas caem juntas; indistinguível de 1 | 55000 escuta, as três sondas mudas ao mesmo tempo → **provável**; a página do CPE do SPA confirma | — | quando o enlace volta |
| 3 | Processo do cliente morreu (F7, exceção, OOM) | `Cliente encerrou a conexao` (FIN do `finally`), nenhum `CONNECT` depois | Pi responde → Pi viva | texto da exceção, RSS antes | **sim** com `Restart=always` |
| 4 | Cliente travado (Chrome do `restart_router`, captura) | `Timeout`, nenhum `CONNECT` depois | Pi responde | processo vivo sem enviar | não (precisa do auto-teste do agente) |
| 5 | Câmera / cabo CSI / Picamera2 | ciclos `CONNECT`→`Cliente encerrou` sem frame | Pi responde | `logs_failure`: `erro no envio: ...` | não |
| 6 | Enlace da câmera saturado (F8) — a 106 | `degraded`, `flapping`, `Cliente encerrou` (sendall de 6 s) | Pi responde, RTT alto | **RSRP/RSRQ do CPE local** — o único lugar onde o rádio da câmera é legível | n/a |
| 7 | CPE / SIM / rádio da câmera fora — Pi viva e isolada | `Timeout`, silêncio | Pi **e** poste mudos | anel continua: "estou viva, WAN morta"; página do CPE local | quando o rádio volta |
| 8 | Pi travada (kernel, I/O do SD) — a assinatura do projeto | `Timeout`, silêncio | Pi muda, **poste responde** (se o RST for do CPE — ver 3.4) | precursores: iowait, processos em D, temperatura, `mmc` no dmesg; depois, `boot_id` novo | **sim** com watchdog de hardware |
| 9 | Poste sem energia (Pi + CPE) | `Timeout`, silêncio | Pi e poste mudos — igual a 7 | anel para com valores saudáveis; brownout antes deixa `throttled`/tensão | quando a energia volta, se houver autostart |
| 10 | Pi reiniciou e não voltou (F5) | `Timeout` ou FIN, silêncio | Pi responde (sshd/VNC sobem no boot), cliente não | `boot_id` novo, `uptime` pequeno | **sim** com autostart |

Leitura da tabela:

- **Do PC, hoje, só 1/2 (em bloco) e 6 são reconhecíveis.** As famílias 3, 4,
  7, 8, 9 e 10 são a mesma coisa: `Timeout` e silêncio. É por isso que o
  alerta da 102 não podia dizer mais do que disse.
- **A sonda separa em três grupos**: {1 vs 2}, {3, 4, 5, 10: Pi viva} vs {7,
  8, 9: caminho morto}, e — se o RST da porta fechada vier do CPE — {8: poste
  responde} vs {7, 9: poste mudo}. Isso já muda o que se manda alguém fazer:
  3/10 se resolvem por SSH; 8 é reset; 7/9 é subir no poste.
- **Só a caixa-preta vê dentro.** Exceção do cliente, precursor do
  travamento, rádio local, e a resposta à pergunta de 06/07: "o que consertou?"
  (um registro de boot).
- **S4 sozinha muda a coluna da direita** para 3, 8 e 10: de "até alguém ir a
  Santos" para minutos. É a mudança de maior efeito por linha de código —
  são zero linhas.

Sobre o motivo do servidor, que é o dado mais fino que temos hoje (F12,
20/08):

| `DISCONNECT` diz | Significa |
|---|---|
| `Timeout, encerrando conexao` | 30 s sem byte nenhum. Sem FIN: o caminho morreu **antes** de o cliente conseguir fechar |
| `Cliente encerrou a conexao` | FIN. O cliente fechou: processo morreu (o `finally` fecha), ou o `sendall` de 6 s estourou (F8) |
| `Header truncado (n/4 bytes)` | morreu no meio do frame — brownout, rádio |
| `Interrupcao na leitura do frame` | idem, no corpo do frame |

## 3. A 102 agora

### 3.1 O que os dados dizem

Corte seco a 57 f/min, sem degradação. `Timeout` sem FIN: o caminho já estava
morto às 04:37:20. Sozinha: 105 e 106 no ar — não é servidor nem enlace do
SPA. Três reaberturas do servidor sem um `CONNECT` dela: o cliente não está
tentando (o backoff tem teto de 1800 s; em 7 h teria vindo). Famílias
restantes: **7, 8, 9** — e, pela tabela, o PC não as separa.

A estabilidade da 102 pesa aqui. Em julho ela estava em `throttled=0x0`,
59,9 °C, e **nunca tinha gravado um `logs_failure`** (a pasta não existia).
Causa de software é improvável; hardware, energia ou rádio é o esperado.

### 3.2 Uma ressalva ao diário de 24/08

O diário conclui "uma Pi viva atende a porta 22; a da 102 não atende nada".
Os inventários de `docs/evidencias/fase0/` (102 em julho, 105 e 106 em
julho/agosto) mostram as unidades em execução nas três, e **nenhuma delas roda
`ssh.service`** — `vncserver-x11-serviced` sim, sshd não. Então quem responde
em `192.168.10.105:22` e `.106:22`?

- Se for a Pi (sshd ligado depois do inventário), o raciocínio do diário vale.
- Se for o próprio CPE — a Teltonika da 105 tem SSH no roteador de fábrica —
  então "`.102:22` mudo" diz que o **CPE** da 102 não responde, e não diz nada
  sobre a Pi. Isso empurra a hipótese para 7/9 (poste ou rádio), não para 8.

Não dá para decidir daqui. O banner da porta decide em cinco segundos:
`SSH-2.0-OpenSSH_9.2p1 Debian` é a Pi; `SSH-2.0-dropbear` é a Teltonika.
E a porta **5900** (VNC) roda nas três Pis segundo o inventário — se ela
responder `RFB 003.008`, é a Pi sem ambiguidade, e vira o sinal de vida
preferido da sonda.

### 3.3 Teste de dois minutos, do PC

Somente leitura: cada tentativa é um handshake TCP e fecha. Na 55000 nem
isso — o servidor descarta uma conexão sem ID sem gravar linha nenhuma
(`client_session`: `if not id_len: close`).

```powershell
@'
import socket, time

ALVOS = {"102": "192.168.10.102", "105": "192.168.10.105", "106": "192.168.10.106"}
PORTAS = (22, 5900, 81)     # ssh, vnc, e uma que ninguem deve atender
EPC = "192.168.10.1"

def tenta(host, porta, timeout=3.0):
    t0 = time.time()
    try:
        s = socket.create_connection((host, porta), timeout)
    except ConnectionRefusedError:
        return "RECUSADA - alguem vivo respondeu RST", time.time() - t0, b""
    except (socket.timeout, TimeoutError):
        return "TIMEOUT  - ninguem respondeu", time.time() - t0, b""
    except OSError as e:
        return f"ERRO {e.errno}: {e.strerror}", time.time() - t0, b""
    banner = b""
    try:
        s.settimeout(2.0)
        banner = s.recv(80)
    except OSError:
        pass
    s.close()
    return "ABERTA", time.time() - t0, banner

for cam, ip in ALVOS.items():
    for porta in PORTAS:
        estado, dt, banner = tenta(ip, porta)
        print(f"{cam}  {ip}:{porta:<5} {estado:<40} {dt*1000:6.0f} ms  {banner.strip()!r}")
for porta in (80, 81):
    estado, dt, _ = tenta(EPC, porta)
    print(f"EPC  {EPC}:{porta:<5} {estado:<40} {dt*1000:6.0f} ms")
'@ | Set-Content -Encoding ascii "$env:USERPROFILE\Desktop\sonda.py"
& "C:\ProgramData\anaconda3\python.exe" "$env:USERPROFILE\Desktop\sonda.py"
ping -n 2 192.168.10.1
tracert -d -w 1000 -h 6 192.168.10.102
```

Como ler, calibrando primeiro nas duas que estão vivas:

| Nas 105/106 | Conclusão |
|---|---|
| `:5900 ABERTA` com `RFB` | a Pi responde pela 5900 → sinal de vida da sonda |
| `:22 ABERTA` com `OpenSSH ... Debian` | a Pi responde pela 22 |
| `:22 ABERTA` com `dropbear` | é o roteador, não a Pi |
| `:81 RECUSADA` | alguém no poste responde RST a porta fechada → **existe sonda de poste** |
| `:81 TIMEOUT` | o CPE descarta o que não encaminha; sonda de CPE não dá por essa via |

| Na 102 | Conclusão |
|---|---|
| `:81 RECUSADA`, resto `TIMEOUT` | alguém no poste vivo, Pi muda → **travada ou sem energia só ela** (8). Reset resolve |
| tudo `TIMEOUT` | ninguém no poste responde → **energia do poste, CPE, SIM ou rádio** (7/9) |
| `tracert` para no `192.168.10.1` ou antes | o problema é do lado de cá do EPC — revisar a conclusão "sozinha" |

Rodar duas vezes com um minuto de intervalo e colar a saída no diário. Não
traz a 102 de volta — decide **o que levar** para o poste, e calibra os
parâmetros da S1. O que olhar ao chegar lá está no diário de 24/08 §1.4;
acrescento: fotografar os LEDs do CPE antes de encostar em qualquer coisa, e
conferir a energia do poste antes da Pi.

### 3.4 Resultado — 25/08

| Alvo | :22 | :5900 | :81 |
|---|---|---|---|
| 102 | TIMEOUT 3019 ms | TIMEOUT 3006 ms | TIMEOUT 3008 ms |
| 105 | ABERTA 152 ms — `SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u6` | ABERTA 120 ms — `RFB 005.000` | RECUSADA 2344 ms |
| 106 | ABERTA 196 ms — `SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3` | ABERTA 122 ms — `RFB 005.000` | RECUSADA 2380 ms |
| `192.168.10.1` | TIMEOUT em 80 e 81; `ping` com 100% de perda | | |

`tracert` até a 102: salto 1 em `192.168.11.254`, depois nada até o limite.

O que ficou decidido:

- **A porta 22 é a Pi.** OpenSSH do Debian 12 nas duas — sshd foi ligado
  depois dos inventários, e a ressalva de 3.2 cai para a 105 e a 106. As
  versões diferem (`deb12u6` na 105, `deb12u3` na 106): mais um dado do F6.
  A 5900 também é a Pi (RealVNC, RFB 5.0). A 22 fica como sinal de vida da
  sonda, porque o banner carrega a versão.
- **Um poste vivo recusa a porta 81; o da 102 não responde nada.** É o sinal
  que faltava, com uma ressalva que o teste não fecha: o RST pode vir do CPE
  (porta fechada na WAN dele) ou da Pi (se o CPE encaminha tudo, DMZ). Só a
  página de encaminhamento do CPE da 106 — alcançável por SSH nela, em
  `192.168.11.254` — diz qual. Até lá a métrica se chama `reach_poste`,
  "alguém no poste responde", e não `reach_cpe`.
- **O gateway LTE não responde ao PC.** Em 2025 as Rasps o pingavam a 35 ms,
  do lado delas; do lado do SPA, nada. `reach_epc` sai da S1.
- **O Windows leva ~2,3 s para reportar uma recusa**: retransmite o SYN
  depois do RST antes de devolver `ECONNREFUSED` (o mesmo teste contra
  `127.0.0.1` leva 2,0 s). Com timeout de 3 s, uma recusa sob RTT alto vira
  TIMEOUT — poste vivo lido como morto. A sonda usa 5 s.
- O diário de 24/08 registra o `Route_add.bat` apontando para
  `192.168.11.25`; o primeiro salto do `tracert` é `192.168.11.254`. Conferir
  com `route print 192.168.10.0`. Não muda nada aqui, mas a memória do projeto
  tem que bater com a máquina.

**Para a 102.** Nada no poste responde, enquanto os outros dois postes
respondem. Famílias **7 e 9** — energia do poste, CPE, SIM ou rádio. A 8 (Pi
travada com CPE viva) só continua possível se o RST da 81 vier da Pi via DMZ,
e mesmo aí não é a primeira coisa a olhar. Ao chegar lá: energia do poste e
LEDs do CPE antes da Pi; se a Pi estiver ligada, `uptime`, `dmesg -T | tail
-50` e `router.log` **antes** de reiniciar qualquer coisa (diário de 24/08
§1.4).

**Para a S4.** Com sshd respondendo na 105 e na 106, autostart, NTP, F7 e o
watchdog podem ser aplicados **por SSH, hoje**, sem descida — 106 primeiro,
como a spec manda.

## 4. Soluções

### S1 — Sonda do PC

Um worker novo no monitor (`probe.py`), no mesmo molde de `disk()` e `cpe()`.

| Alvo | Como | Métrica gravada |
|---|---|---|
| Pi de cada câmera | TCP à 22 em `192.168.10.10x` (banner OpenSSH confirmado em 3.4), timeout 5 s | `reach_pi` 0/1, `reach_pi_ms` |
| Poste de cada câmera | TCP à 81; `RECUSADA` = alguém no poste vivo (CPE ou Pi via DMZ — ver 3.4) | `reach_poste` 0/1 |
| Servidor de imagens | TCP a `192.168.11.101:55000` | `server_listening` 0/1 |

O gateway LTE `192.168.10.1` não responde ao PC (3.4): não há sonda do enlace
do SPA por essa via. A família 2 é lida pela simultaneidade — as três sondas
mudas de uma vez com a 55000 escutando — e confirmada pela página do CPE do
SPA quando o scraper estiver calibrado.

Cadência de 60 s. **Ao detectar `down`, sondar a câmera na hora**, dentro do
ciclo do alerta, para a mensagem já sair com o resultado em vez de esperar o
próximo minuto.

Sobre o princípio "push, nunca pull": ele nasceu de "as Rasps estão atrás do
NAT; o PC não as alcança", que o `Route_add.bat` desmentiu. A sonda é um
handshake por minuto, não toca processo nenhum na Rasp, e a falha dela é o
sinal. O princípio que fica é outro: **pull para observar, nunca para
controlar**.

Calibração foi pré-requisito, não detalhe — e foi feita em 25/08 (3.4). Sem o
banner e o teste de porta fechada, a sonda seria o ping — "não prova nada" —
e um `reach_pi=0` numa câmera cujo CPE não encaminhasse aquela porta viraria
um veredito falso. Da calibração vieram dois parâmetros: o timeout de 5 s
(o Windows leva ~2,3 s para reportar uma recusa, e 3 s transformaria poste
vivo em morto) e as portas. A configuração é por câmera, em `config.toml`:

```toml
[probe]
enabled = true
interval_s = 60
timeout_s = 5
server = "192.168.11.101:55000"

[probe.cameras.102]
host = "192.168.10.102"
pi_port = 22            # banner OpenSSH: e' a Pi (3.4)
poste_closed_port = 81  # RST = alguem no poste vivo (3.4)
```

Custo: um dia, com testes usando um `connect` de mentira. Nada muda na Rasp.

### S2 — Diagnóstico e linha do tempo no bot

Um módulo puro (`diagnose.py`) sobre a store: dada uma câmera e uma queda,
devolve **famílias possíveis, grau de certeza e as evidências usadas**. As
regras, na ordem em que o código as avalia:

1. **Cobertura.** Se o monitor estava fora na hora, diz isso primeiro e não
   conclui nada sobre esse trecho. Ignorância pintada de veredito é pior que
   silêncio.
2. **Simultaneidade.** Duas ou mais câmeras caídas na mesma janela de 3 min →
   famílias 1/2. `server_listening` e `SERVER_START` separam; sem sonda, fica
   "servidor ou enlace do SPA".
3. **Motivo do servidor** (tabela da seção 2): `Timeout` → caminho morreu
   antes do cliente; `Cliente encerrou` → o cliente fechou; truncado → morreu
   no meio do frame.
4. **Antes.** `frames_min` contra a base da hora nos 30 min anteriores:
   "degradou antes" (aponta 6) ou "corte seco" (aponta 7/8/9).
5. **Depois.** Houve `CONNECT` nos 30 min seguintes? Ciclos
   `CONNECT`/`DISCONNECT` sem frame → 5. `SERVER_START` sem `CONNECT` da
   câmera → o cliente não está tentando.
6. **Sonda.** `reach_pi=1` → {3, 4, 5, 10}; `reach_pi=0` → {7, 8, 9};
   `reach_poste=1` com `reach_pi=0` → 8; ambos 0 → {7, 9}.
7. **Caixa-preta** (quando existir): último push antes da queda, precursores,
   `boot_id` novo.

A saída tem quatro blocos — **Antes / Na hora / Depois / Veredito** — e o
veredito sempre nomeia o que não separou e o que separaria. Três lugares:

- `/queda <câmera>`: a queda em curso ou a última. Formato:

```
Queda da 102 - 24/08 04:37, ha 1d09h, ainda fora
Antes   03:37-04:37  57 f/min, estavel (base da hora 57). Corte seco.
        04:37:20     ultimo frame
Na hora 04:37:56     Timeout (30 s sem bytes). Sem FIN: o caminho morreu
                     antes do cliente.
        105, 106     no ar durante toda a janela
Depois  11:12:53     SERVIDOR INICIADO - 105 e 106 reconectaram; a 102 nao
        11:28:04     idem
        19:06        monitor reiniciado (sem cobertura por alguns minutos)
Veredito: caminho ate a 102 morto e cliente sem tentar. Nao e' servidor
nem enlace do SPA. Sem sonda nao separo Pi travada / poste sem energia /
CPE fora - a sonda ao poste separaria a primeira das outras duas.
```

- **Dentro do alerta `down`**, na versão curta. O que a 102 teria recebido às
  04:40 com S1 e S2 no ar:

```
Camera 102 SEM FRAMES ha 3min.
Antes: 57 f/min ate 04:37:20, estavel. Corte seco.
Na hora: Timeout as 04:37:56, sem FIN. 105 e 106 no ar.
Sonda: Pi 192.168.10.102 muda desde 04:38; poste mudo (nem RST na 81).
Veredito: poste inteiro mudo - energia, CPE ou radio. 105 e 106 respondem:
nao e' servidor nem enlace do SPA.
```

- **Nos lembretes silenciosos** de meia em meia hora: só a linha da sonda, e
  só se mudou ("CPE voltou a responder às 09:12; Pi continua muda").

Testes: regras contra stores sintéticas, uma por família; e o replay de 24/08
(`test_integration_replay.py` já tem a estrutura) tem que produzir o texto
acima, palavra por palavra.

### S3 — O gráfico do alerta mostra o antes

A spec pedia "gráfico de 30 min (frames/min, RSRP, eventos de conexão)". O
que sai hoje é a faixa de 24 h de conexão. Para uma queda, a imagem útil são
as **2 h anteriores**: `frames/min` em cima, idade do frame embaixo, ticks de
`DISCONNECT` e a queda marcada. Os botões continuam levando para as outras
janelas. É uma renderização nova com dois painéis (`charts.py`) e uma linha em
`alert_chart`. Meio dia.

### S4 — Na Rasp, sem código novo

Tudo isto já está em `deploy/rasp/` e nunca foi aplicado:
`camera-client.service` (`Restart=always`), `porto-ntp.conf`, o
`watchdog_test.py` blindado (F7). Acrescento **uma linha**:

```ini
# /etc/systemd/system.conf
RuntimeWatchdogSec=60
```

O Pi 4 tem watchdog de hardware (`bcm2835_wdt`). Com isso, se o PID 1 parar
de afagar o cão — kernel travado, I/O do SD parado — o SoC reinicia a Pi
sozinho. Cobre a família 8, que é a do projeto (a Rasp da eNodeB de julho),
com zero código. Não cobre "userspace travado com o systemd bem" — para isso
é o auto-teste do agente da spec, que vem depois.

Efeitos:

- Famílias 3, 8 e 10 passam de "até alguém ir a Santos" para minutos.
- A queda ganha uma **marca**: a câmera reconecta depois de minutos de
  silêncio, e a caixa-preta (S5) vê `boot_id` novo. Isso é o dado que faltou
  em 06/07.
- Ressalva do relógio: com RTC morto (102 e 106) e NTP inativo, a Pi acorda
  com hora errada até sincronizar — e sincronizar depende do drop-in mais o
  encaminhamento UDP 123 no CPE do SPA (runbook B4). A caixa-preta não pode
  confiar no relógio da Rasp (ver S5).

Aplicar por SSH se a calibração de 3.3 confirmar que é a Pi quem atende e
houver credencial; senão, na descida. Ordem da spec: 106 primeiro, uma semana
em observação, depois 105, e a 102 quando voltar.

### S5 — Caixa-preta (Fase 3), reposicionada

Fica da spec: stdlib apenas, processo único, `OOMScoreAdjust=-1000`, lê
`/sys` e `/proc` sem `fork`, push HTTP ao PC em `:55001` com backlog local,
publica os últimos 30 min de antes do boot ao subir, ausência de push por
90 s é o heartbeat perdido.

Muda:

- **Registro local em JSONL** com teto de tamanho e rotação, `fsync` por
  linha. O anel binário pré-alocado da spec sobrevive a travamento tanto
  quanto isto, e custa muito mais para ler no campo com `tail`.
- **Cada linha carrega `boot_id` e `uptime_s`**, e o PC carimba a chegada. O
  relógio da Rasp não entra na correlação — está errado em duas das três.
- **O payload leva os logs que a Pi já grava e ninguém lê**: arquivos novos
  em `~/Desktop/logs_failure/` (a linha `Motivo:`), linhas novas de
  `router.log`, o `tail` do stderr do cliente (journal, sob systemd), e linhas
  novas de `dmesg` que casem com `mmc|Undervoltage|oom|hung task`. Em 20/08 a
  106 tinha dezenas de `logs_failure` por dia contando exatamente o que o
  servidor não via.
- **CPE local**: TCP a `192.168.11.254:80` (viva?) e, quando calibrado,
  RSRP/RSRQ raspados da página dela — o único lugar onde o rádio da câmera é
  legível (diário de 20/08 §7). É o que responde à pergunta do navio.
- **Processo do cliente**: PID, RSS, e se está enviando (bytes de rede da
  interface).

No PC: um `ingest` (thread `http.server` no próprio monitor) que grava
`samples`/`events` com `origin` = câmera. A S2 ganha a visão de dentro sem
mudar de forma — só ganha a regra 7.

Precisa de: encaminhamento `:55001` no CPE do SPA (temos acesso), Python 3.11
nas Pis (tem), instalação por SSH ou descida. Plano próprio, como a spec
previa.

O que fecha: 3/4/5 pelo texto da exceção; 8 pelos precursores; 9 contra 8
pelo rastro de brownout e pelo "saudável e depois nada"; 6 pelo RSRP local; e
a pergunta de 06/07 — "o que consertou?" — por um registro de boot.

### S6 — Pull sob demanda por SSH (complemento)

Se a Pi atender SSH e houver chave: quando uma câmera cai com `reach_pi=1`,
o monitor busca `systemctl status camera-client`, `journalctl -u camera-client
-n 40`, os três `logs_failure` mais novos (linha `Motivo:`), `tail -5
router.log`, `vcgencmd get_throttled`, `uptime`, e anexa ao alerta. Chave
restrita (`command=` no `authorized_keys`, script somente leitura), timeout de
10 s, nunca bloqueia o alerta.

Mais barato que a S5 para as famílias "Pi viva" (3, 4, 5, 10). Inútil para as
que mais importam (7, 8, 9) — portanto não a substitui. Cabe entre a S3 e a
S4 se a credencial existir.

## 5. Teto de exatidão

O que continua sem resposta mesmo com tudo aplicado:

- **Energia vs. travamento.** Os dois param o registro com valores saudáveis.
  O que separa: brownout antes deixa `throttled`/tensão no anel; e, na volta,
  o watchdog reinicia ~60 s depois do travamento **com o CPE no ar**, enquanto
  a queda de energia leva o CPE junto. O discriminador é a sonda ao poste (S1)
  — desde que o RST venha do CPE, ver 3.4 — não nada dentro da Pi.
- **Rádio derrubado do lado da eNodeB para uma SIM só.** Do PC parece CPE
  morta; de dentro da Pi parece WAN morta. Só a página do CPE local (S5) diz
  "registrada sem portadora" ou "sem sinal".
- **Por que o poste perdeu energia.** Nada digital responde.

Por isso o veredito tem três graus, e o bot diz qual: **certo** (as
evidências contradizem todas as outras famílias), **provável**, e **não
separável sem X** — com o X nomeado.

## 6. Ordem recomendada

| Quando | O quê | Depende de |
|---|---|---|
| feito, 25/08 | teste da seção 3.3 — resultado em 3.4 | — |
| esta semana, só PC | S1 → S2 → S3, nesta ordem | nada: calibrada |
| agora, por SSH | S4 na 106 (sshd responde; credencial do `pi`) | ler antes a página de encaminhamento do CPE dela |
| opcional | S6 | chave SSH |
| depois, plano próprio | S5 | encaminhamento `:55001`, S4 aplicada |

## 7. O que muda nos documentos

- README, "Princípios": "push, nunca pull" vira "pull para observar, nunca
  para controlar", com a premissa corrigida.
- Spec, Fase 3: reescopar conforme S5.
- Runbook `fase1-campo.md`, bloco C: acrescentar `RuntimeWatchdogSec=60`.
- Diário de 24/08 §1.3: a porta 22 é a Pi (banner OpenSSH, 25/08) — a
  conclusão vale para 105 e 106. Para a 102, o que se sabe é que nada no
  poste responde, nem a porta fechada que os outros dois recusam.
- Diário de 24/08 §2.2 e memória: `Route_add.bat` aponta para
  `192.168.11.25`, mas o primeiro salto do `tracert` é `192.168.11.254`.
  Conferir com `route print 192.168.10.0`.
