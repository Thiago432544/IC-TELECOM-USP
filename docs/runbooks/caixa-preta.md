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

1. **Copiar** o pacote `caixa-preta-AAAA-MM-DD.zip` para a Rasp. Pelo VNC:
   no RealVNC Viewer, botão de transferência de arquivos → enviar para
   `/home/pi`. Na Rasp: `cd ~ && unzip caixa-preta-*.zip` (cria
   `/home/pi/caixa-preta/`). Sem o pacote, copiar de `deploy/rasp/` pelo menos
   `caixa_preta.py`, `caixa-preta.service`, `journald-persistente.conf` e
   `instalar_caixa_preta.sh` para essa pasta.
2. **Instalar** (num terminal da Rasp).

   > **Atenção na 102:** o `.bashrc` dela termina com
   > `python3 .../2026_02_05_Cliente_H01.py`. Todo terminal novo **inicia um
   > segundo cliente** com o mesmo ID. Ao abrir o terminal, aperte **Ctrl+C
   > uma vez** para parar esse cliente novo (só o dele) e usar o prompt. Nunca
   > feche nem dê Ctrl+C na janela antiga, onde roda o cliente de produção.
   > Conferir que sobrou um só: `pgrep -af Cliente_H01`.

   ```bash
   cd /home/pi/caixa-preta
   sudo bash instalar_caixa_preta.sh 102
   ```
   O gateway é detectado sozinho pela rota padrão (`.254` Elsys, `.1` Teltonika).
3. **Conferir**: o fim da saída mostra `active` e as primeiras linhas do
   relatório — `inicio`, `historico` (o fim do `router.log` e os últimos
   `logs_failure`, guardados de uma vez), `estado_inicial` (conexão
   `ESTABLISHED` e o PID do cliente) e `relogio` (com `offset_s` perto de
   +3 460 000 s, os ~40 dias de atraso).

Ordem: **102 agora**; 105 quando houver janela; 106 quando ela voltar.

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
cd /home/pi/caixa-preta
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

## Do lado do PC do SPA

- O monitor precisa estar no ar para o lado do servidor da linha do tempo
  (`LOG_connections.txt`, frames por minuto). A tarefa agendada nunca foi
  registrada e isso custou 53 h de silêncio em 28/08:
  `.\deploy\pc\install_monitor_task.ps1` como Administrador.
- O relógio do PC está certo: é a referência para cruzar com a Pi.

## Depois de uma queda: o que coletar

Na Rasp, **antes de reiniciar qualquer coisa**:

```bash
python3 /opt/caixa-preta/caixa_preta.py --relatorio --horas 12 > ~/queda.txt
journalctl -u camera-client --since "-12h" >> ~/queda.txt     # nível 2
journalctl -k --since "-12h" >> ~/queda.txt
tail -80 ~/Desktop/Porto_de_Santos_2025/PRINCIPAL/router.log >> ~/queda.txt
tar czf ~/caixa-preta-$(hostname).tgz /var/log/caixa-preta ~/queda.txt
```

E no PC: as linhas da câmera no `LOG_connections.txt` na mesma janela.

O relatório imprime cada evento com a hora da Pi e a **hora real estimada**
(pelo último `offset_s` do evento `relogio`), para cruzar direto com o PC.

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

## Volume

~2–3 MB por dia por câmera. Arquivos de 20 MB, mantidos os 100 mais novos
(~2 GB, mais de dois anos). A 102 tem 306 GB livres.
