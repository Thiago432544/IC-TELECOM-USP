# Monitoramento das Câmeras — Porto de Santos

Sistema de monitoramento para as Raspberry Pis das câmeras do Porto de Santos
(projeto IC TELECOM USP / CILIP). Detecta quedas e degradação das câmeras,
avisa no Telegram com gráfico do que aconteceu antes, e serve um painel local.

> ⚠️ **Repositório privado.** Documenta topologia de rede, endereçamento e
> fragilidades conhecidas de uma infraestrutura em operação. Não tornar público.

## Por que existe

Em 06/07/2026 uma reunião de FMEA virou o diagnóstico ao vivo de uma falha real:
a Raspberry da eNodeB travou e o sistema só voltou em 18/08 — sem que ninguém
soubesse dizer o que o consertou. O histórico de pastas em `D:\SPA_Data` mostra
o padrão se repetindo: buracos de set→dez/2025, 55 dias em abr–mai/2026,
27/jul→05/ago e 06→18/ago.

O diagnóstico de campo de 19/08 (evidências em `docs/evidencias/fase0/`)
encontrou, entre outras coisas, que a câmera 106 caía e reconectava
**588 vezes por dia** sem que ninguém percebesse — porque ela sempre voltava.

Contexto completo: [`docs/superpowers/specs/`](docs/superpowers/specs/).

## As três câmeras não são intercambiáveis

| Câmera | CPE | `save_every` | Observação |
|---|---|---|---|
| 102 | Elsys | 10 | rádio próprio |
| 105 | Teltonika | 1 | monitora particulados: manda menos imagens e maiores |
| 106 | Elsys | 10 | bem mais distante da eNodeB que a 102 |

Cada uma tem **rádio e CPE próprios** — a queda de uma não diz nada sobre as
outras, e `restart_router()` de uma reinicia só o CPE dela. O `192.168.11.254`
que aparece como gateway em mais de uma Rasp é endereço de LAN de fábrica, não
um equipamento compartilhado (MACs distintos, diário de 20/08). Confundir isso
já custou um diagnóstico errado em 24/08.

O `save_every` iguala a cadência de gravação das três em ~1 arquivo a cada 10 s
apesar dos ritmos de envio muito diferentes — é por isso que um mesmo piso de
intervalo serve para as três, e por isso que baixá-lo multiplicaria por 10 o
disco da câmera que mais envia.

## Estado

| Fase | O que é | Estado |
|------|---------|--------|
| **1 — Estancar** | Correções no que já está quebrado (timeout, autostart, watchdog, NTP) | Servidor do PC **corrigido e no ar** desde 20/08 22:33 (F2, F11, F12, F13). Autostart, NTP e itens de hardware nas Rasps: **pendentes** |
| **2 — Ver e avisar** | Coletor + bot Telegram + painel, no PC do SPA | Monitor **rodando** desde 19/08 16:26. Telegram **mudo** — `enabled` nunca verificado |
| **3 — Caixa-preta** | Agente nas Rasps + watchdog de hardware | Planejada, não iniciada |

O servidor do PC já roda as correções. O que falta depende de ir a campo, e a
ordem está nos runbooks. Estado detalhado e catálogo de achados no diário mais
recente: [`docs/diario/2026-08-20-sessao.md`](docs/diario/2026-08-20-sessao.md).

## Como começar

```bash
pip install -r requirements-dev.txt
python -m pytest                       # 43 testes
```

Para instalar no PC do SPA, siga na ordem:

1. [`docs/runbooks/fase1-campo.md`](docs/runbooks/fase1-campo.md) — as correções
2. [`docs/runbooks/fase2-pc.md`](docs/runbooks/fase2-pc.md) — o monitor

```powershell
copy config.example.toml config.toml   # preencher token e chat_id do Telegram
python -m monitor                      # painel em http://localhost:8080
```

Os caminhos padrão do `config.example.toml` já são os do PC do SPA.
`config.toml` está no `.gitignore` — segredos nunca entram no repositório.

## Estrutura

```
monitor/          o coletor (roda no PC Windows do SPA)
  watcher.py        frames/min por câmera, lendo D:\SPA_Data (somente leitura)
  taplog.py         segue o LOG_connections.txt do servidor de imagens
  cpe.py            RSRP/RSRQ do CPE ELSYS via HTTP
  store.py          SQLite: série temporal + eventos, retenção de 90 dias
  baseline.py       linha de base por câmera e hora do dia (mediana de 7 dias)
  alerts.py         máquina de estados dos alertas, com anti-ruído
  uptime.py         intervalos sem imagem, a partir da idade do último frame
  metrics.py        registry de métricas e o piso de queda por janela
  telegram.py       Bot API + fila em disco para quando a internet cai
  charts.py         faixa de conexão e séries; PNG anexado ao alerta
  bot.py            /status, /metricas e /grafico <câmera> [métrica] [janela]
  panel.py          painel HTTP local
  summary.py        resumo diário das 08:00
  service.py        fiação: threads supervisionadas

deploy/pc/        patches e instaladores do PC do SPA
deploy/rasp/      unit systemd, NTP e watchdog blindado das Rasps
  caixa-preta/      registro de rede, cliente e sistema dentro da Pi
  auto-reparo/      destrava o caminho de rede sozinho (o "cabo" por software)
docs/evidencias/  diagnósticos de campo que embasaram o design
docs/runbooks/    passo a passo de aplicação
docs/diario/      registro por sessão: o que foi feito, achado e refutado
docs/superpowers/ spec (design aprovado) e plano de implementação
tests/            pytest — roda inteiro sem depender do Porto
```

## O bot no Telegram

```
/status                    estado atual das três câmeras
/grafico 106               conexão da 106 nas últimas 24h
/grafico 106 2h            outra janela
/grafico 106 frames 12h    outra métrica
/grafico 2h 106            a ordem dos argumentos não importa
/grafico 105 rsrp_cam      RSRP do rádio da própria câmera
/grafico 105 potencia 12h  o mesmo, por apelido e noutra janela
/grafico rsrp              RSRP do CPE do SPA — sem número de câmera
/grafico rsrq 2h           RSRQ do CPE do SPA, outra janela
/grafico disco             espaço livre no PC do SPA
/metricas                  o que dá para plotar
```

`rsrp`, `rsrq` e `disco` **não pertencem a câmera nenhuma** — o rádio lido é o
do CPE do lado do SPA, e o disco é o do PC. Exigir o número da câmera nelas era
pedir um dado que não existe, e a resposta virava o texto de ajuda. Se você
passar um número mesmo assim, ele é ignorado e a legenda diz `cpe`, não `106`:
carimbar a câmera ali faria parecer o rádio da Rasp.

**`rsrp_cam` e `rsrp` medem pontas diferentes do mesmo enlace.** O primeiro é o
rádio da própria câmera, lido de dentro da Rasp; o segundo é o CPE do lado do
SPA. O sufixo existe porque confundir as duas pontas já custou um diagnóstico
errado aqui. O `/status` mostra o da câmera numa linha própria, quando há
leitura recente.

O caminho do dado da câmera tem uma volta: **o PC não alcança o `192.168.11.1`**,
que é a LAN atrás do rádio. Quem fala com o CPE é o `deploy/rasp/radio_log.py`,
que roda na Rasp por cron e mantém um CSV; o monitor só busca a última linha
dele por SSH, e precisa de chave. Sem chave não há leitura, e o `/status` diz
isso — em vez de pendurar pedindo senha.

Enquanto `[cpe] enabled = false` no `config.toml`, esses dois gráficos saem
vazios **dizendo o porquê** — o coletor está desligado, não é a janela que está
errada. Para ligar, calibre os regexes contra a página real do CPE:

```powershell
python -m monitor.cpe --probe
```

O gráfico chega com botões de `30min · 1h · 2h · 12h · 24h`; tocar troca a
imagem na mesma mensagem em vez de mandar outra.

**O gráfico mede o registro de imagem, não a conexão.** Cheio = havia imagem
nova chegando, vazado = não havia, cinza = o monitor não estava no ar
(ignorância nunca é pintada de verde). Até 24h é uma faixa só; acima disso vira
uma linha por dia, para comparar o mesmo horário entre dias.

**As desconexões são uma coisa separada, e aparecem separadas.** Até 24h, numa
pista própria embaixo da faixa — marcas individuais enquanto der para separar,
densidade acima disso, porque 150 marcas viram uma tarja preta que não informa
nada. Acima de 24h a marca não cabe, então cada dia leva o número à direita.

Os dois números lado a lado na legenda respondem a pergunta que importa:

| Leitura | Significado |
|---|---|
| intervalos ≫ desconexões | enlace **estrangulado** — conectado, entregando devagar |
| intervalos ≈ desconexões | enlace **caindo** |

Foi assim que se descobriu que as "36 quedas em uma hora" da 106 conviviam com
apenas ~6 desconexões: o enlace não estava caindo, estava estrangulado.

**O piso acompanha o zoom.** Um intervalo só entra no gráfico se durar mais que
o piso da janela — senão as centenas de eventos de instabilidade da 106 voltam a
saturar o desenho, que era o problema do gráfico de frames/min.

| Janela | Piso | O que você vê |
|--------|------|---------------|
| 30min, 1h, 2h | 30 s | cada respiro |
| 12h | 2 min | interrupção de verdade |
| 24h | 5 min | interrupção de verdade |
| 7d | 30 min | só pane |

O piso nunca desce de 30 s: `save_every` é ajustado para as três câmeras
gravarem ~1 arquivo a cada 10 s apesar de enviarem em ritmos diferentes (a 105
monitora particulados, manda menos imagens e maiores; a 102 e a 106 mandam
muito mais). Então a idade do último arquivo de uma câmera saudável já oscila
até ~10 s. O piso aparece sempre na legenda (`>=5min`), e `/grafico 106 24h 30s`
força outro na mão.

`save_every` está declarado em dois lugares — no servidor e no `config.toml` do
monitor, que multiplica por ele para calcular `frames/min`. Mudar um sem o outro
deixa a taxa 10× errada em silêncio.

### Por que `frames/min` é medido em 5 minutos, e não entre dois polls

O coletor varre a pasta a cada 10 s. Enquanto a taxa era calculada nesse mesmo
intervalo, cada arquivo visto valia `save_every × 60 / 10` — **60 f/min na 106**.
A métrica só conseguia assumir 0, 60, 120 ou 180: não existia valor entre eles.
Uma câmera entregando 30 f/min de verdade virava uma onda quadrada batendo entre
0 e 120 a cada dez segundos, com média certa e leitura impossível.

Isso estragava três coisas de uma vez:

- **o gráfico**, que virava um bloco azul sólido de ponta a ponta;
- **o alerta de degradada**, que exige o valor abaixo do limiar por 600 s
  *contínuos* — e o contador zerava a cada poll que pegava um arquivo. Na
  cadência nominal de 10 s, uma câmera na metade do normal por uma hora inteira
  dispararia **zero** alertas;

  > Como se sabe a data em que isso acabou: os valores de `frames_min` mudam de
  > granularidade. Antes são múltiplos de 60; depois, múltiplos de 2. O primeiro
  > valor fora do padrão antigo é `29,997` em **22/08 11:47:29** — sete minutos
  > depois do commit. Alertas de degradada anteriores a essa marca são suspeitos;
  > posteriores são medição de verdade.

- **a linha de base**, que é a mediana desses valores. Em horas de entrega mais
  lenta a mediana era `0`, e `frames < 0,5 × 0` nunca é verdade: nessas horas o
  alerta estava desligado por construção.

Hoje a taxa é a contagem de arquivos numa janela deslizante de 5 min
(`RATE_WINDOW_S` em `watcher.py`), o que derruba o degrau de 60 f/min para 2
f/min. A média não muda — só a variância. Trocar a janela é uma linha.

O poll é regular a ponto de o `span` fechar em 300 s cravados: medidos 17.270
intervalos em 48 h, **p50 = p90 = p99 = 10,0 s**, e a mediana não varia entre
as 24 horas do dia. Duas leituras saem daí. A primeira é que o `_scan` não é
caro, apesar de reestatar a pasta do dia inteira (a da 105 passa de 60 mil
arquivos) — no Windows o `os.scandir` já traz o `stat` de cada entrada, sem
syscall extra. A segunda é que, com `span` de 300 s exatos, `frames/min` vira
`2 × (arquivos em 5 min)`: uma câmera entregando exatamente 6 arquivos/min
marca 60,0 cravado por horas seguidas. **Muitos valores "redondos" ali indicam
regularidade, não quantização** — é o contrário do sintoma antigo.

`cpu`, `temperatura` e `memoria` já estão no registry e respondem *"depende do
agente da Fase 3"* — o dado é de dentro da Rasp e não existe caminho para ele
hoje.

### Quando o alerta cala, e quando ele insiste

Em 48 h o sistema gerou **140 alertas, ~135 deles da 106** em tempestade:
`down` → `recovered` → `down` em ciclos de dois minutos, por horas. No meio
disso, a 102 caiu às 04:37 de 24/08 e **não voltou mais** — e essa mensagem, a
única que exigia ação, chegou como uma linha entre dezenas. Detecção nunca foi o
problema; o problema é que o canal ficou inútil de tão cheio.

Duas regras corrigem isso, ambas em `alerts.py`:

- **Flapping cala o par `down`/`recovered` da mesma câmera.** Se a câmera já
  ultrapassou `flap_count` desconexões na janela, o `flapping` *é* o alerta:
  trinta `down` depois dele não acrescentam nada. Quando a tempestade passa e a
  câmera está no ar, sai um `recovered` de encerramento — silêncio sem desfecho
  seria pior que ruído.
- **Queda sustentada insiste** a cada `realert_s`, com a duração acumulada
  (`SEM FRAMES ha 15h05`). Antes, o `down` só disparava na *transição*: uma vez
  dentro do conjunto `_down`, a câmera nunca mais avisava. Uma queda de 15 horas
  rendia uma única mensagem, e o silêncio seguinte era indistinguível de
  problema resolvido.
- **A insistência vai muda.** O primeiro `down` notifica; os lembretes de meia
  em meia hora saem com `disable_notification` e ficam no chat sem vibrar o
  celular. Insistir não é acordar: quem recebeu o primeiro aviso já sabe da
  queda, e vibrar a noite inteira por um problema conhecido ensina a ignorar o
  alerta — aí o próximo, o que importa, também passa batido. O `recovered` e o
  `group_down` continuam notificando, porque são novidade, não repetição. O
  silêncio acompanha a mensagem até pela outbox: um lembrete que ficou preso
  não chega vibrando horas depois.

O `group_down` — duas ou mais câmeras caindo ao mesmo tempo — **não** é calado
pelo flapping. Simultaneidade entre rádios independentes é diagnóstico próprio
(enlace, eNodeB), e a tempestade de uma câmera não explica a outra.

### Por que reiniciar o monitor não infla a contagem de desconexões

O offset do `LOG_connections.txt` vive só na memória do processo
(`taplog.py`). Todo restart relê o arquivo desde o byte zero — e, enquanto
`add_event` era um `INSERT` puro, isso reinseria o histórico inteiro: a
contagem de desconexões saía multiplicada por quantas vezes o monitor já tinha
subido. Como desconexão é evento e a pista do gráfico mostra exatamente esse
número, o gráfico mentia proporcionalmente ao número de deploys.

A correção não foi persistir o offset, e sim tornar o evento idempotente:
índice único em `(ts, origin, kind, detail)` e `INSERT OR IGNORE`. Bancos
gravados pela versão anterior são limpos na primeira abertura. Com isso a
releitura inverte de sinal — deixa de duplicar o que já estava gravado e passa
a **recuperar** o que aconteceu enquanto o monitor esteve fora do ar, que é o
comportamento que sempre se quis do arquivo truncado ou rotacionado.

O preço é colapsar dois eventos idênticos no mesmo segundo (mesmo cliente,
mesmo detalhe) num só. Perder essa marca custa menos que inflar todas as
outras.

## Princípios de projeto

- **Push, nunca pull.** As Rasps estão atrás do NAT dos próprios CPEs; o PC não
  as alcança. A telemetria sai delas — e a ausência dela é, em si, o alarme.
- **Observação passiva primeiro.** O coletor lê o que o pipeline já produz, em
  somente leitura. Zero risco para a produção.
- **O monitor não pode causar a falha.** O agente da Fase 3 é processo único sem
  `fork`, protegido do OOM killer, com anel de disco pré-alocado.
- **Linha de base, não limiar fixo.** A 105 a 10 s/frame é normal; a 102 a
  3 s/frame é emergência. Cada câmera aprende o próprio normal, por hora do dia.

## Pendências

- Confirmar se a câmera 105 pode migrar da variante `sem_grav_local` para a H01
- Criar o bot no @BotFather e definir quem recebe alerta
- Itens de campo: fonte da 106 (subtensão confirmada), bateria dos RTCs da 102 e
  106, diagnóstico da Rasp da COW/eNodeB
- Endereço e protocolo reais da eNodeB (o `192.168.10.100` não responde)
- Decisão de produto: 3 câmeras a 1 fps/q90 não cabem no enlace de 5 MHz
