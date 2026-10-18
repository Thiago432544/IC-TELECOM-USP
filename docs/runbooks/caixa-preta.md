# Runbook — caixa-preta nas Rasps das câmeras

Objetivo: se uma câmera cair de novo, ter registrado **o que aconteceu, em que
ordem e a que horas**, sem depender de alguém reconstruir à mão.

## Por que agora (14/10/2026)

A 102 voltou depois de 50 dias fora. A saída coletada nela mostrou:

- a Pi ficou ligada o tempo todo (`up 54 days`, desde 11/07 no relógio dela);
- a última linha do `router.log` é `Erro ao reiniciar o roteador: ... Chrome
  failed to start` — o gatilho do **F7**, que mata o cliente;
- o último `logs_failure` (`falha_2026-07-15_03-32-36.txt`, relógio da Pi) cai,
  com a defasagem de 40 d 1 h 04 min 44 s, em **24/08 04:37** — o minuto em
  que a 102 parou de entregar imagem;
- o cliente atual nasceu 14 min depois de o cabo do CPE ser desconectado e
  reconectado (cabo fora por 21 s; o CPE não foi reiniciado).

O que faltou para fechar o caso sem dedução: o **erro do cliente** quando ele
morreu (roda num terminal, o erro some), a **hora** de cada linha do
`router.log`, e o **estado da rede vista de dentro da Pi** no momento da queda.

## O que cada peça registra

| Peça | Onde fica | O que responde |
|---|---|---|
| `caixa_preta.py` (nível 1) | `/var/log/caixa-preta/*.jsonl` | gateway e CPE respondem? conexão com o servidor aberta? bytes saindo ou presos na fila? cliente vivo, desde quando? throttled, temperatura, boot, relógio |
| journal persistente (nível 1) | `/var/log/journal` | kernel (cartão SD, subtensão, rede), reinícios, os eventos da caixa-preta em duplicata |
| cliente sob systemd (nível 2) | `journalctl -u camera-client` | **o traceback do cliente quando ele morre**; reinício automático em 10 s |
| `watchdog_test.py` do repositório (nível 2) | `router.log` | **hora em cada linha**; o F7 deixa de matar o cliente |

## Nível 1 — só observar (sem risco, fazer já)

Não toca no cliente nem na transmissão. Pode ser feito com a câmera no ar.

1. **Copiar** a pasta `deploy/rasp/` do repositório para `/home/pi/rasp` na
   Rasp (zip pelo botão de transferência de arquivos do RealVNC Viewer, e
   `cd ~ && unzip <arquivo>.zip`). Dentro dela ficam `caixa-preta/` e
   `auto-reparo/`, cada uma com o seu instalador; o auto-reparo usa a pasta
   `caixa-preta/` ao lado.
2. **Instalar** (num terminal da Rasp).

   > **Atenção na 102:** o `.bashrc` dela termina com
   > `python3 .../2026_02_05_Cliente_H01.py`. Todo terminal novo **inicia um
   > segundo cliente** com o mesmo ID. Ao abrir o terminal, aperte **Ctrl+C
   > uma vez** para parar esse cliente novo (só o dele) e usar o prompt. Nunca
   > feche nem dê Ctrl+C na janela antiga, onde roda o cliente de produção.
   > Conferir que sobrou um só: `pgrep -af Cliente_H01`.

   ```bash
   cd ~/rasp/caixa-preta
   sudo bash instalar_caixa_preta.sh 102
   ```
   O gateway é detectado sozinho pela rota padrão (`.254` Elsys, `.1` Teltonika).
   Se aparecer `set: pipefail: invalid option` (ou `\r` em algum erro), os
   arquivos passaram pelo Windows e ganharam fim de linha CRLF. Em `~/rasp`:
   `sed -i 's/\r$//' */*.sh */*.py */*.service */*.conf *.sh *.py *.service *.conf`
   e rodar de novo.
3. **Conferir**: o fim da saída mostra `active` e as primeiras linhas do
   relatório — `inicio`, `historico` (o fim do `router.log` e os últimos
   `logs_failure`, guardados de uma vez), `estado_inicial` (conexão
   `ESTABLISHED` e o PID do cliente) e `relogio` (a data que a Elsys informa;
   nesta rede ela não acerta a hora e diz 2023, e o relatório a ignora — ver
   "Hora real" abaixo).

Ordem: **102 agora**; 105 quando houver janela; 106 quando ela voltar.

## Auto-reparo — manter o caminho (e o VNC) de pé

Em 24/08 a Elsys da 102 parou de encaminhar o tráfego por 50 dias, e o VNC
morreu junto; tirar e recolocar o cabo resolveu. O `auto_reparo.py` faz isso
sozinho: a cada minuto testa o caminho até o PC do SPA (`192.168.10.101:55000`,
o mesmo encaminhamento por onde o VNC entra) e, com o cabo ligado, sobe uma
escada:

| Tempo sem caminho | O que faz |
|---|---|
| 10 min | renegocia o endereço (`nmcli connection up`) |
| 20 min | derruba a placa de rede por 15 s e levanta — o "cabo" por software |
| depois | nova rodada com espera de 1 h, 2 h, 4 h, 6 h; no máximo 8 ações por dia |

Também reinicia o serviço do RealVNC se ele parar. Não reinicia a Elsys e não
mexe no cliente da câmera (isso é o nível 2).

O caminho conta como de pé se o cliente está conectado ou se a conexão de teste
**abre**. Recusa conta como ruim: o servidor sempre escuta na 55000, então um
RST vem de alguém no meio — em 24/08, a própria Elsys.

Instalar:

```bash
cd ~/rasp/auto-reparo
sudo bash instalar_auto_reparo.sh 102              # já agindo
sudo bash instalar_auto_reparo.sh 102 --observar   # só registra o que faria
```

Sem queda, o journal só mostra `auto_reparo_inicio`. Os eventos (`caminho_ruim`,
`reparo`, `caminho_ok` com `resolvido_por`) entram no mesmo `--relatorio` da
caixa-preta. O `resolvido_por` responde a pergunta aberta em 14/10: se a 102
volta no degrau 1, bastava a renegociação de endereço; se só no 2, era preciso
o link cair.

Suspender durante manutenção: `sudo touch /etc/auto-reparo.pausa` (retomar:
`sudo rm /etc/auto-reparo.pausa`).

## Sinal da Elsys — RSRP a cada 30 s (17/10/2026)

A caixa-preta lê RSRP e RSRQ da página de status da Elsys pela LAN, a cada
`radio_s` segundos (padrão 30; começou em 5 e foi espaçado em 18/10, quando a
102 caiu de vez menos de um dia depois — até saber se há relação). Funciona
com o LTE fora, que é quando importa.
A pergunta que isso responde: na hora da queda, o sinal tinha caído antes
(obstáculo, como um caminhão) ou estava bom e o tráfego parou mesmo assim (a
Elsys)?

- O firmware novo pede login: a caixa-preta usa o **mesmo `router.conf`** do
  `watchdog_test.py` (`usuario = admin`, `senha = ...`, ao lado do script do
  cliente). Sem ele, registra `radio_sem_leitura` e passa a tentar só a cada
  5 min.
- Enquanto o watchdog reinicia a Elsys (`router.lock`), a leitura para.
- Cada leitura vai para `/var/log/caixa-preta/radio.jsonl` (~1,2 MB/dia),
  separado da linha do tempo. Nela entram só `radio_caiu`/`radio_voltou` (RSRP
  10 dB abaixo da linha de base) e falhas de leitura.
- A página traz IMEI e ICCID; eles nunca vão para o registro.
- Se alguém precisar usar a página da Elsys no navegador da Pi e a sessão cair
  a toda hora, pare a caixa-preta durante o uso (`sudo systemctl stop
  caixa-preta`, e `start` depois).

```bash
python3 /opt/caixa-preta/caixa_preta.py --radio            # uma leitura: testa o login
tail -3 /var/log/caixa-preta/radio.jsonl                   # uma linha a cada 30 s
python3 /opt/caixa-preta/caixa_preta.py --quedas --horas 24 # RSRP em volta de cada queda
```

O `--quedas` mostra, para cada `wan_perdida`, as leituras de 90 s antes a 30 s
depois e um veredito: "o sinal caiu N dB antes da queda: obstáculo provável"
ou "o sinal estava estável antes da queda: aponta para a Elsys".

## Nível 2 — cliente sob systemd (recomendado; decisão do time)

Muda como o cliente roda. Ganho: o erro que mata o cliente fica gravado, o
cliente volta sozinho em 10 s, e o F7 deixa de existir. Custo: uma
interrupção de segundos ao trocar o cliente manual pelo serviço.

**Antes**, desligar o autostart atual. Na 102 ele é o fim do `.bashrc`
(confirmado em 14/10: o pai do cliente é um `bash` nascido no mesmo segundo),
que inicia o cliente a cada boot no tty1 **e a cada terminal aberto**. Com o
serviço instalado, ele subiria um segundo cliente com o mesmo ID. Nas outras
câmeras, conferir:

```bash
P=$(pgrep -f Cliente_H01 | head -1); ps -o pid,ppid,lstart,cmd -p $P
ps -o pid,lstart,cmd -p $(ps -o ppid= -p $P)
ls ~/.config/autostart 2>/dev/null; crontab -l 2>/dev/null; tail -5 ~/.bashrc
```

Para desligar, comentar as duas linhas finais do `.bashrc`
(`nano ~/.bashrc`, pôr `#` na frente de `echo Running at boot` e de
`python3 .../Cliente_H01.py`). Isso não para o cliente que já roda. Depois:

```bash
cd ~/rasp
sudo bash install_camera_client.sh        # instala o serviço e o watchdog_test.py blindado
# parar o cliente manual: Ctrl+C na janela dele (ou: kill $(pgrep -f Cliente_H01))
sudo systemctl start camera-client
journalctl -u camera-client -f            # "Conectado ao servidor..."
```

No PC do SPA: `CONNECT` da câmera no `LOG_connections.txt` em até ~20 s.
O `watchdog_test.py` original fica guardado como `watchdog_test.py.original-*`.

Na 105 (Teltonika) o `router.conf` com modelo, usuário e senha precisa existir
antes — o instalador avisa. A 105 já tem uma cópia blindada própria; conferir
com o time antes de trocar.

### O `watchdog_test.py` e o firmware da Elsys

Desde 16/10/2026 a Elsys da 102 roda um firmware novo, com tela de login e o
botão de reiniciar oculto. O `watchdog_test.py` do repositório reconhece os
dois firmwares pela página. O novo exige a senha num `router.conf` ao lado do
script — **nunca no código**, o repositório é público:

```bash
cd ~/Desktop/Porto_de_Santos_2025/PRINCIPAL
printf 'usuario = admin\nsenha = <senha de campo da Elsys>\n' > router.conf
chmod 600 router.conf
```

Validar sem derrubar nada: `python3 watchdog_test.py --seco` faz o login e
acha o botão sem clicar. A saída diz o firmware, o botão achado e termina em
`--seco ok`. Depois de qualquer atualização de firmware, rodar de novo.
Nenhum botão com cara de reset de fábrica é clicado.

## Do lado do PC do SPA

- O monitor precisa estar no ar para o lado do servidor da linha do tempo
  (`LOG_connections.txt`, frames por minuto). A tarefa agendada nunca foi
  registrada e isso custou 53 h de silêncio em 28/08:
  `.\deploy\pc\install_monitor_task.ps1` como Administrador.
- O relógio do PC está certo: é a referência para cruzar com a Pi.

## Depois de uma queda: o que coletar

Na Rasp, **antes de reiniciar qualquer coisa**:

```bash
python3 /opt/caixa-preta/caixa_preta.py --relatorio --horas 12 > ~/queda.txt   # + --offset, se já calibrado
journalctl -u camera-client --since "-12h" >> ~/queda.txt     # nível 2
journalctl -k --since "-12h" >> ~/queda.txt
tail -80 ~/Desktop/Porto_de_Santos_2025/PRINCIPAL/router.log >> ~/queda.txt
tar czf ~/caixa-preta-$(hostname).tgz /var/log/caixa-preta ~/queda.txt
```

E no PC: as linhas da câmera no `LOG_connections.txt` na mesma janela.

O relatório imprime cada evento com a hora da Pi e a **hora real estimada**,
para cruzar direto com o PC.

### Hora real

O relógio da Pi está errado (sem RTC e sem NTP), e **o da Elsys também**: a
rede da NLT não tem internet, a Elsys nunca acerta a hora: dizia 2023 e voltava
horas a cada reinício (102, 14/10); com o firmware novo passou a dizer março de
2026 (17/10). O relatório nunca usa esse relógio e deixa a coluna da hora real
com `?`. Para preenchê-la, calibre pelo
PC do SPA, que tem a hora certa:

1. No relatório, pegue um evento `conexao_servidor` em que a conexão abriu
   (`"para": ["ESTABLISHED"]`) e anote a hora da Pi.
2. No `LOG_connections.txt`, pegue o `CONNECT` da câmera correspondente.
3. Rode de novo com o par (Pi=real):

```bash
python3 /opt/caixa-preta/caixa_preta.py --relatorio --horas 2 \
    --offset "2026-09-04 21:29:58=2026-10-14 22:34:03"
# ou, já sabendo a defasagem:  --offset 40d1h4m5s
```

A amostra é a cada 30 s, então a calibração tem ±30 s. A defasagem só muda
quando a Pi perde energia (o `fake-hwclock` volta à última hora salva): depois
de um `boot_novo`, calibre de novo.

## Como ler os eventos

| O relatório mostra | Leitura provável |
|---|---|
| `router_log` "Erro ao reiniciar..." seguido de `cliente_sumiu` | **F7**: o cliente morreu ao tentar reiniciar o roteador |
| `cliente_sumiu` sem nada no `router_log` | o cliente morreu por outro motivo; ver o traceback no `journalctl -u camera-client` |
| `cabo` 1 → 0 e `gateway_perdido` | cabo ou energia do CPE |
| `gateway` ok, `wan_perdida`, `conexao_servidor` fecha | o CPE está vivo na LAN mas perdeu o rádio/LTE |
| `gateway` ok, `wan` ok, conexão fecha, `sonda_servidor` timeout | caminho depois do CPE: eNodeB, CPE do SPA ou servidor |
| `fila_presa` | conexão aberta, mas os bytes não saem: o enlace morreu com a conexão aberta (vira `Timeout` no servidor) |
| `arp_gateway` ok → incompleta, com o cabo ligado | **o CPE travou do lado da LAN** — a hipótese da Elsys |
| `LACUNA de N s sem amostra` | a própria Pi parou (travamento, I/O do cartão) |
| `boot_novo` | a Pi reiniciou; `ultima_amostra_t` diz quando ela parou |
| `throttled` diferente de `0x0` | subtensão ou temperatura |
| `reparo` seguido de `caminho_ok` | o auto-reparo destravou; `resolvido_por` diz com qual degrau |
| `reparo` repetido sem `caminho_ok` | o problema não se resolve de dentro da Pi: rádio, eNodeB, energia da Elsys ou o PC do SPA |
| `radio_caiu` pouco antes de `wan_perdida` | o sinal despencou antes da queda: obstáculo (confirme com `--quedas` e as fotos) |
| `wan_perdida` sem `radio_caiu` antes | o rádio estava bom quando o tráfego parou: a Elsys |
| `radio_sem_leitura` | a caixa-preta não consegue ler a página da Elsys: `router.conf` sem senha, senha errada, ou a Elsys fora da LAN |
| `wan_perdida` com o gateway respondendo, e `caminho_ruim` com `erro:101` | **a Elsys perdeu o LTE**: ela responde "rede inalcançável" para o PC (102, 14/10 22:28) |
| `router_log` "reinicializando roteador", depois `gateway_perdido` e `Link is Down` no kernel | o watchdog do cliente reiniciou a Elsys de verdade (só funciona com o Chrome abrindo: cliente num terminal do VNC ou o `watchdog_test.py` do repositório) |
| dois "reinicializando" seguidos, com o LTE voltando entre eles | o watchdog original reiniciando a cada tentativa: o segundo reinício derruba o LTE que acabou de voltar |

## Volume

~2–3 MB por dia por câmera. Arquivos de 20 MB, mantidos os 100 mais novos
(~2 GB, mais de dois anos). A 102 tem 306 GB livres.
