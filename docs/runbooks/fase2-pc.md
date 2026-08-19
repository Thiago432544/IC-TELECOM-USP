# Runbook — Fase 2 no PC do SPA

Pré-requisito: Fase 1 aplicada (runbook fase1-campo.md).

1. Clonar/copiar o repo para o PC (ex.: `C:\monitor\repo`).
2. `pip install -r requirements.txt`
3. `copy config.example.toml config.toml` e editar:
   - criar o bot: @BotFather → `/newbot` → colar token;
   - criar o grupo, adicionar o bot, pegar o chat_id
     (https://api.telegram.org/bot<TOKEN>/getUpdates após mandar uma msg no grupo);
   - `telegram.enabled = true`.
4. Teste manual: `python -m monitor` → painel abre em http://localhost:8080;
   mandar `/status` no grupo e conferir a resposta.
5. CPE (opcional agora): `python -m monitor.cpe --probe` → se os regexes não
   capturarem, ajustar `rsrp_re`/`rsrq_re` no config com o HTML impresso;
   quando capturar, `cpe.enabled = true`.
6. Instalar como tarefa: PowerShell admin → `deploy\pc\install_monitor_task.ps1`.
7. Validação de aceite (spec §5 Fase 2): matar o cliente de uma câmera →
   alerta com gráfico chega no Telegram em ≤4 min; painel reflete; `/status`
   responde; reiniciar o PC → monitor volta sozinho.
