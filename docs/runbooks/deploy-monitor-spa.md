# Runbook — atualizar o monitor no PC do SPA

Para subir o gráfico de conexão e o `/grafico` com botões (commit `10733f9`).

## 0. O que pode e o que não pode quebrar

O monitor é **somente leitura**. Ele não escreve em `D:\SPA_Data`, não toca no
`LOG_connections.txt` e não fala com o servidor de imagens. Se este deploy der
errado, o pior resultado é **ficar sem monitoramento por alguns minutos**. As
câmeras continuam entregando.

Existe **um** jeito de quebrar o Porto neste procedimento: parar o processo
errado. Há dois `python.exe` na máquina.

| Processo | O que é | O que fazer |
|---|---|---|
| `...2026_02_01_Server_H00.py` | **o servidor de imagens** | **não encoste** |
| `...-m monitor` | o monitor | este sim |

Toda vez que um comando abaixo pedir um PID, confira a `CommandLine` antes de
apertar Enter.

Nada de migração de banco: o schema não mudou. O gráfico novo lê o
`last_frame_age_s` que já vem sendo gravado desde 19/08 16:26 — o histórico
aparece sozinho assim que o código subir.

## 1. Ver o que está rodando (não muda nada)

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CreationDate, CommandLine | Format-List
```

Anote o `ProcessId` da linha que tem `-m monitor`. Se aparecer um argumento
depois de `-m monitor` (um caminho de config), anote também — é o config que
ele usa de verdade.

## 2. Achar a pasta do repositório

O config fica **na pasta do repositório**, não em `C:\monitor\`, porque o
`-m monitor` sem argumento lê `config.toml` relativo ao diretório de trabalho.

```powershell
Get-ChildItem C:\Users\CILIP -Recurse -Depth 5 -Filter "config.toml" -ErrorAction SilentlyContinue |
  Select-Object FullName, LastWriteTime
```

Guarde o caminho da pasta que contém esse `config.toml` — é a raiz do repo.
Confirme que a tarefa agendada aponta para a mesma pasta:

```powershell
Get-ScheduledTask -TaskName MonitorCamerasPorto -ErrorAction SilentlyContinue |
  Select-Object State,
    @{n='Exe';e={$_.Actions.Execute}},
    @{n='Args';e={$_.Actions.Arguments}},
    @{n='Pasta';e={$_.Actions.WorkingDirectory}}
```

- **Devolveu uma linha** → o monitor sobe por tarefa agendada. Siga o caminho A
  no passo 5.
- **Não devolveu nada** → ele está rodando de uma sessão manual, e vai morrer no
  próximo reboot mesmo sem este deploy. Caminho B no passo 5.

## 3. Descobrir se a pasta é clone ou ZIP

```powershell
Set-Location "<a pasta do passo 2>"
git status
```

- **Mostrou o status** → é clone. Passo 4A.
- **`not a git repository`** → é um ZIP baixado. Passo 4B.

## 4A. Atualizar (clone)

```powershell
Copy-Item .\config.toml "$env:USERPROFILE\Desktop\config.toml.bak" -Force
git log --oneline -1        # anote este hash: e' o seu rollback
git pull
git log --oneline -3
```

O topo tem que ser `10733f9`. O `config.toml` é ignorado pelo git, então o
`pull` não encosta nele — a cópia é cinto de segurança.

## 4B. Atualizar (ZIP)

Recomendo trocar por um clone agora; assim o próximo deploy vira um comando só
e some o risco de perder o config numa troca de pasta.

```powershell
Copy-Item "<pasta atual>\config.toml" "$env:USERPROFILE\Desktop\config.toml.bak" -Force
git clone https://github.com/Thiago432544/IC-TELECOM-USP.git C:\monitor\app
Copy-Item "$env:USERPROFILE\Desktop\config.toml.bak" C:\monitor\app\config.toml
```

A pasta nova precisa ter as dependências:

```powershell
Set-Location C:\monitor\app
python -m pip install -r requirements.txt
```

E a tarefa precisa apontar para ela (no passo 5, caminho A, registre de novo com
`deploy\pc\install_monitor_task.ps1` rodando **de dentro de `C:\monitor\app`**,
como Administrador).

Se preferir não migrar agora: baixe o ZIP novo, extraia ao lado, copie o
`config.toml` da pasta antiga para a nova, e aponte a tarefa para a nova pasta.

## 5. Reiniciar

### Caminho A — tarefa agendada

```powershell
Stop-ScheduledTask -TaskName MonitorCamerasPorto
Start-ScheduledTask -TaskName MonitorCamerasPorto
```

### Caminho B — sessão manual

Confira o PID uma última vez antes de parar:

```powershell
Get-CimInstance Win32_Process -Filter "ProcessId=<PID do passo 1>" |
  Select-Object ProcessId, CommandLine | Format-List
```

Só se a `CommandLine` disser `-m monitor`:

```powershell
Stop-Process -Id <PID do passo 1>
Start-Process python -ArgumentList "-m monitor" -WorkingDirectory "<a pasta>"
```

Aproveite para instalar a tarefa agendada e não depender mais de sessão manual
(como Administrador, de dentro da pasta do repo):

```powershell
.\deploy\pc\install_monitor_task.ps1
```

## 6. Conferir que subiu o código novo

Não basta ver um `python.exe` no ar — é a mesma armadilha do `SERVIDOR INICIADO`
de 20/08. Confira por algo que **só a versão nova sabe fazer**:

**No Telegram:**

```
/metricas
```

Esse comando não existe na versão antiga. Se ele responder a lista de métricas,
o código novo está carregado. Depois:

```
/grafico 106 7d
```

Tem que chegar a faixa por dia, com os botões `30min · 1h · 2h · 12h · 24h`
embaixo, e a janela vigente marcada com `•`. Toque em `24h`: a **mesma** imagem
tem que trocar, sem mandar mensagem nova.

**No PC:**

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CreationDate, CommandLine | Format-List
Invoke-WebRequest http://localhost:8080/api/status -UseBasicParsing |
  Select-Object -ExpandProperty Content
```

O `CreationDate` do monitor tem que ser de agora. E o servidor de imagens tem
que continuar lá, com o `CreationDate` **antigo** — se ele mudou, alguma coisa o
reiniciou e vale olhar o log de conexões.

## 7. Se der errado

```powershell
Set-Location "<a pasta>"
git checkout <o hash que voce anotou no passo 4A>
Stop-ScheduledTask -TaskName MonitorCamerasPorto
Start-ScheduledTask -TaskName MonitorCamerasPorto
```

O banco não precisa de nada: nenhuma tabela mudou, e a versão antiga volta a
ler exatamente o que lia antes.

## Observação sobre a fila do Telegram

O offset do bot mora em memória. Ao reiniciar, ele pega os updates não
confirmados da fila do Telegram e responde todos de uma vez. Isso já acontece
hoje, não é novidade desta versão — mas com botões pode chegar uma rajada no
grupo se alguém tiver mandado comando durante a parada.

Para evitar, mande um `/status` **depois** que o monitor voltar e antes de
qualquer outra pessoa mexer: a primeira leitura drena a fila. Ou simplesmente
ignore a rajada, que é inofensiva.
