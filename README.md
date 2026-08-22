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
/metricas                  o que dá para plotar
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

`cpu`, `temperatura` e `memoria` já estão no registry e respondem *"depende do
agente da Fase 3"* — o dado é de dentro da Rasp e não existe caminho para ele
hoje.

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
