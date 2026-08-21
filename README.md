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
  telegram.py       Bot API + fila em disco para quando a internet cai
  charts.py         PNG dos últimos 30 min anexado ao alerta
  bot.py            /status e /grafico <id>
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
