# Monitoramento das Rasps (Fases 1 e 2) — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estancar as falhas conhecidas do pipeline de câmeras (Fase 1) e construir o coletor + bot Telegram + painel no PC do SPA (Fase 2), conforme a spec.

**Architecture:** Fase 1 são patches mínimos no código legado (trazido para o repo em `deploy/`) + units systemd + NTP, aplicados em campo via runbook. Fase 2 é um pacote Python `monitor/` que roda no PC Windows: observa passivamente `D:\SPA_Data` e `LOG_connections.txt`, guarda séries em SQLite, avalia regras de alerta com linha de base por hora do dia e fala com o Telegram. Tudo push/somente-leitura sobre o pipeline de produção.

**Tech Stack:** Python 3.12 (PC) / 3.11 (Rasp), stdlib + `requests` + `matplotlib` (só no PC), SQLite (WAL), systemd (Rasp), w32time + Agendador de Tarefas (Windows), pytest.

**Spec:** `docs/superpowers/specs/2026-08-19-monitoramento-rasps-design.md`

**Fora deste plano:** Fase 3 (caixa-preta nas Rasps) — plano próprio depois que as Fases 1–2 estiverem em campo.

## Global Constraints

- O monitor abre TUDO do pipeline em **somente leitura**; nenhum código ou teste escreve em `D:\SPA_Data` ou `LOG_connections.txt` reais.
- **Push, nunca pull:** o PC nunca inicia conexão com as Rasps.
- Dependências de terceiros no PC: apenas `requests` e `matplotlib` (+`pytest` em dev). Rasp: stdlib.
- `config.toml` NUNCA vai para o git (token do Telegram, senha do CPE); `config.example.toml` versionado.
- Timestamps internos: unix epoch `float` (UTC); exibição em hora local.
- Anti-ruído: re-alerta da mesma condição só após 30 min (disco: 24 h) ou mudança de estado.
- Retenção: amostras brutas 90 dias; agregado por hora, indefinido.
- Valores exatos da spec: timeout do servidor **30 s**; câmera caiu = **0 frames por 3 min**; degradada = **<50% da linha de base por 10 min**; flapping = **≥5 DISCONNECT em 15 min**; enlace ruim = **RSRP < −110 dBm ou RSRQ < −15 dB por 5 min**; disco = **< 60 GB livres**; resumo diário **08:00**; linha de base = **mediana por hora do dia, 7 dias, mínimo 20 amostras**.
- Fonte do código legado: `C:\Users\Thiago\Downloads\porto_santos.zip` (não versionado; os arquivos patchados em `deploy/` sim).
- Dados de produção conhecidos: `save_every` = 10 (câmeras 102 e 106) e 1 (câmera 105); imagens em `D:\SPA_Data\Imagens_Porto\<YYYY_MM_DD>\<id>\`; log em `D:\SPA_Data\LOG_connections.txt`; CPE do SPA em `http://192.168.10.101` (web UI Amplimax, acesso interno pelo PC via gateway).

## Estrutura de arquivos

```
monitor/                    pacote Python do coletor (PC)
  __init__.py
  config.py                 carrega config.toml → dataclasses
  store.py                  SQLite: samples, events, samples_hourly, retenção
  taplog.py                 parser + seguidor do LOG_connections.txt
  watcher.py                frames/min por câmera a partir de D:\SPA_Data
  baseline.py               mediana por hora do dia (7 dias)
  alerts.py                 máquina de estados dos alertas
  charts.py                 PNGs matplotlib (Agg)
  telegram.py               cliente da API + fila outbox
  cpe.py                    scraper HTTP do Amplimax + CLI de sondagem
  bot.py                    comandos /status e /grafico
  panel.py                  painel http local :8080
  summary.py                resumo diário 08:00
  service.py                fiação de threads + loop principal
  __main__.py               python -m monitor
tests/                      pytest; fixtures em tests/fixtures/
deploy/
  pc/Servidor_receb_Imagens_Rasp.py   servidor legado com timeout 30 s
  pc/enable_ntp_server.ps1            w32time como servidor NTP
  pc/install_monitor_task.ps1         tarefa agendada do monitor
  rasp/watchdog_test.py               restart_router() blindado
  rasp/camera-client.service          unit systemd do cliente de câmera
  rasp/porto-ntp.conf                 drop-in do systemd-timesyncd
  rasp/install_camera_client.sh       instalador (unit + NTP)
docs/runbooks/
  fase1-campo.md            checklist de aplicação em campo
  fase2-pc.md               instalação do monitor no PC
config.example.toml
requirements.txt            requests, matplotlib
requirements-dev.txt        pytest
.gitignore                  config.toml, data/, __pycache__/ ...
```

---

### Task 1: Esqueleto do repo + configuração

**Files:**
- Create: `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `config.example.toml`, `monitor/__init__.py`, `monitor/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `monitor.config.load_config(path: Path) -> Config`. Dataclasses (todas `@dataclass(frozen=True)`):
  - `CameraCfg(save_every: int)`
  - `PathsCfg(images: Path, log: Path, data: Path)`
  - `TelegramCfg(enabled: bool, token: str, chat_id: str)`
  - `CpeCfg(enabled: bool, url: str, username: str, password: str, rsrp_re: str, rsrq_re: str, interval_s: int)`
  - `AlertCfg(disk_min_gb: float, down_after_s: int, degraded_ratio: float, degraded_after_s: int, flap_count: int, flap_window_s: int, realert_s: int, link_rsrp_min: float, link_rsrq_min: float, link_after_s: int)`
  - `PanelCfg(port: int)`
  - `Config(cameras: dict[str, CameraCfg], paths: PathsCfg, telegram: TelegramCfg, cpe: CpeCfg, alerts: AlertCfg, panel: PanelCfg, summary_hour: int)`

- [ ] **Step 1: Criar arquivos de base**

`.gitignore`:
```gitignore
config.toml
data/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
```

`requirements.txt`:
```
requests>=2.31
matplotlib>=3.8
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8
```

`config.example.toml` (defaults = valores da spec):
```toml
# Copie para config.toml e preencha os segredos. config.toml NÃO vai para o git.
summary_hour = 8

[paths]
images = 'D:\SPA_Data\Imagens_Porto'
log = 'D:\SPA_Data\LOG_connections.txt'
data = 'C:\monitor\data'            # SQLite, outbox, logs do próprio monitor

[cameras.102]
save_every = 10                     # servidor grava 1 a cada 10 frames
[cameras.105]
save_every = 1
[cameras.106]
save_every = 10

[telegram]
enabled = false                     # true depois de criar o bot no @BotFather
token = "COLOQUE_O_TOKEN_AQUI"
chat_id = "COLOQUE_O_CHAT_ID_AQUI"

[cpe]
enabled = false                     # true depois de calibrar com: python -m monitor.cpe --probe
url = "http://192.168.10.101/"
username = ""
password = ""
rsrp_re = '(-?\d+)\s*dBm\s*\(RSRP\)'
rsrq_re = '(-?\d+)\s*dB\s*\(RSRQ\)'
interval_s = 60

[alerts]
disk_min_gb = 60.0
down_after_s = 180
degraded_ratio = 0.5
degraded_after_s = 600
flap_count = 5
flap_window_s = 900
realert_s = 1800
link_rsrp_min = -110.0
link_rsrq_min = -15.0
link_after_s = 300

[panel]
port = 8080
```

`monitor/__init__.py`: vazio.

- [ ] **Step 2: Teste que falha**

`tests/test_config.py`:
```python
from pathlib import Path
from monitor.config import load_config

def test_load_example_config():
    cfg = load_config(Path("config.example.toml"))
    assert cfg.cameras["102"].save_every == 10
    assert cfg.cameras["105"].save_every == 1
    assert cfg.paths.images == Path(r"D:\SPA_Data\Imagens_Porto")
    assert cfg.telegram.enabled is False
    assert cfg.alerts.down_after_s == 180
    assert cfg.alerts.realert_s == 1800
    assert cfg.panel.port == 8080
    assert cfg.summary_hour == 8
```

- [ ] **Step 3: Rodar e ver falhar** — `python -m pytest tests/test_config.py -v` → FAIL (módulo inexistente).

- [ ] **Step 4: Implementar `monitor/config.py`**

```python
"""Carrega config.toml em dataclasses tipadas."""
from __future__ import annotations
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CameraCfg:
    save_every: int

@dataclass(frozen=True)
class PathsCfg:
    images: Path
    log: Path
    data: Path

@dataclass(frozen=True)
class TelegramCfg:
    enabled: bool
    token: str
    chat_id: str

@dataclass(frozen=True)
class CpeCfg:
    enabled: bool
    url: str
    username: str
    password: str
    rsrp_re: str
    rsrq_re: str
    interval_s: int

@dataclass(frozen=True)
class AlertCfg:
    disk_min_gb: float
    down_after_s: int
    degraded_ratio: float
    degraded_after_s: int
    flap_count: int
    flap_window_s: int
    realert_s: int
    link_rsrp_min: float
    link_rsrq_min: float
    link_after_s: int

@dataclass(frozen=True)
class PanelCfg:
    port: int

@dataclass(frozen=True)
class Config:
    cameras: dict[str, CameraCfg]
    paths: PathsCfg
    telegram: TelegramCfg
    cpe: CpeCfg
    alerts: AlertCfg
    panel: PanelCfg
    summary_hour: int


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config(
        cameras={k: CameraCfg(**v) for k, v in raw["cameras"].items()},
        paths=PathsCfg(**{k: Path(v) for k, v in raw["paths"].items()}),
        telegram=TelegramCfg(**raw["telegram"]),
        cpe=CpeCfg(**raw["cpe"]),
        alerts=AlertCfg(**raw["alerts"]),
        panel=PanelCfg(**raw["panel"]),
        summary_hour=raw["summary_hour"],
    )
```

- [ ] **Step 5: Rodar e ver passar** — `python -m pytest tests/test_config.py -v` → PASS.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: esqueleto do monitor + config tipada"`

---

### Task 2: Fase 1.3 — blindar `restart_router()`

**Files:**
- Create: `deploy/rasp/watchdog_test.py`
- Test: `tests/test_watchdog_patch.py`

**Interfaces:**
- Produces: `deploy/rasp/watchdog_test.py` com `restart_router() -> int` (0 sucesso, −1 falha) que **nunca** levanta exceção — substitui o arquivo homônimo em `~/Desktop/Porto_de_Santos_2025/PRINCIPAL/` das Rasps. Bug original (F7 da spec): `driver.quit()` no `except` estoura `NameError` se `webdriver.Chrome()` falhar, matando o cliente de câmera que o chama.

- [ ] **Step 1: Teste que falha** — selenium não existe na máquina de dev; o teste injeta um fake em `sys.modules` ANTES do import:

`tests/test_watchdog_patch.py`:
```python
import importlib.util
import sys
import types
from pathlib import Path

WD_PATH = Path("deploy/rasp/watchdog_test.py")

def _fake_selenium(chrome_factory):
    """Registra módulos selenium falsos; chrome_factory() é chamado por webdriver.Chrome()."""
    selenium = types.ModuleType("selenium")
    webdriver = types.ModuleType("selenium.webdriver")
    webdriver.Chrome = lambda *a, **k: chrome_factory()
    common_by = types.ModuleType("selenium.webdriver.common.by")
    common_by.By = types.SimpleNamespace(XPATH="xpath")
    chrome_opts = types.ModuleType("selenium.webdriver.chrome.options")
    chrome_opts.Options = lambda: types.SimpleNamespace(binary_location=None)
    chrome_svc = types.ModuleType("selenium.webdriver.chrome.service")
    chrome_svc.Service = lambda *a: None
    selenium.webdriver = webdriver
    for name, mod in {
        "selenium": selenium,
        "selenium.webdriver": webdriver,
        "selenium.webdriver.common": types.ModuleType("selenium.webdriver.common"),
        "selenium.webdriver.common.by": common_by,
        "selenium.webdriver.chrome": types.ModuleType("selenium.webdriver.chrome"),
        "selenium.webdriver.chrome.options": chrome_opts,
        "selenium.webdriver.chrome.service": chrome_svc,
    }.items():
        sys.modules[name] = mod

def _load(chrome_factory, tmp_path, monkeypatch):
    _fake_selenium(chrome_factory)
    spec = importlib.util.spec_from_file_location("watchdog_test", WD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LOG", str(tmp_path / "router.log"))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod

def test_chrome_failing_to_open_does_not_raise(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("chromedriver ausente")
    mod = _load(boom, tmp_path, monkeypatch)
    assert mod.restart_router() == -1        # original: NameError aqui

def test_success_path_quits_driver(tmp_path, monkeypatch):
    calls = []
    class FakeDriver:
        def get(self, url): calls.append(("get", url))
        def find_element(self, by, xp):
            return types.SimpleNamespace(click=lambda: calls.append(("click", xp)))
        def quit(self): calls.append(("quit", None))
    mod = _load(FakeDriver, tmp_path, monkeypatch)
    assert mod.restart_router() == 0
    assert ("quit", None) in calls
    assert sum(1 for c in calls if c[0] == "click") == 2
```

- [ ] **Step 2: Rodar e ver falhar** — `python -m pytest tests/test_watchdog_patch.py -v` → FAIL (arquivo não existe).

- [ ] **Step 3: Escrever `deploy/rasp/watchdog_test.py`** (arquivo completo; mesmos XPaths e URLs do original):

```python
"""Reinicia o CPE ELSYS pelo web UI. Versao blindada do watchdog_test.py original:
- driver inicializado como None: falha do Chrome nao gera mais NameError
- quit() em finally, protegido
- log de router nunca derruba a funcao
Instalar em: /home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/watchdog_test.py
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
import time

LOG = "/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/router.log"

BTN_LOGIN = "/html/body/div[1]/div[2]/form/div[1]/div[3]/input"
BTN_REBOOT = "/html/body/div[1]/div[2]/form/div[5]/div[2]/table/tbody/tr[7]/td[2]/input"


def _log(msg):
    try:
        with open(LOG, "a") as f:
            print(msg, file=f)
    except OSError:
        pass


def restart_router():
    _log("reinicializando roteador")
    driver = None
    try:
        options = Options()
        options.binary_location = "/usr/bin/chromium"
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        driver.get("http://192.168.11.254/index.html?status")
        time.sleep(10)
        driver.find_element(By.XPATH, BTN_LOGIN).click()
        time.sleep(10)
        driver.find_element(By.XPATH, BTN_REBOOT).click()
        _log("roteador reiniciado com sucesso")
        time.sleep(10)
        return 0
    except Exception as e:
        _log(f"Erro ao reiniciar o roteador: {e}")
        return -1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    restart_router()
```

- [ ] **Step 4: Rodar e ver passar** — `python -m pytest tests/test_watchdog_patch.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "fix(fase1): restart_router blindado contra falha do Chrome (F7)"`

---

### Task 3: Fase 1.1 — servidor com timeout de 30 s

**Files:**
- Create: `deploy/pc/Servidor_receb_Imagens_Rasp.py`
- Test: `tests/test_servidor_patch.py`

**Interfaces:**
- Produces: cópia patchada do `rasp_101.py` de produção com `SOCKET_TIMEOUT = 30` como constante de módulo, usada para TODOS os clientes (o `INTERVAL = 15/1` por cliente deixa de existir). Nenhuma outra mudança de comportamento.

- [ ] **Step 1: Extrair o original do zip**

```bash
python - <<'EOF'
import zipfile, pathlib
z = zipfile.ZipFile(r"C:\Users\Thiago\Downloads\porto_santos.zip")
src = z.read("porto_santos/rasp_101.py").decode("utf-8")
pathlib.Path("deploy/pc/Servidor_receb_Imagens_Rasp.py").write_text(src, encoding="utf-8")
EOF
```

- [ ] **Step 2: Teste que falha**

`tests/test_servidor_patch.py`:
```python
import importlib.util
import sys
import types
from pathlib import Path

SRV = Path("deploy/pc/Servidor_receb_Imagens_Rasp.py")

def _load():
    # cv2/numpy nao existem na maquina de dev; o modulo so os usa dentro de funcoes
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    sys.modules.setdefault("numpy", types.ModuleType("numpy"))
    spec = importlib.util.spec_from_file_location("servidor", SRV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)      # seguro: servidor so inicia sob __main__
    return mod

def test_timeout_unico_de_30s():
    mod = _load()
    assert mod.SOCKET_TIMEOUT == 30

def test_interval_por_cliente_foi_removido():
    src = SRV.read_text(encoding="utf-8")
    assert "INTERVAL" not in src
    assert "settimeout(SOCKET_TIMEOUT)" in src
```

- [ ] **Step 3: Rodar e ver falhar** — `python -m pytest tests/test_servidor_patch.py -v` → FAIL.

- [ ] **Step 4: Aplicar o patch mínimo** em `deploy/pc/Servidor_receb_Imagens_Rasp.py`:

Na seção CONFIGURAÇÕES, depois de `PORT = 55000`, adicionar:
```python
# Tempo maximo sem receber dados antes de derrubar a conexao.
# Era 1s (15s para a 105): matava conexoes vivas porem lentas -> Broken pipe
# na camera -> ~588 reconexoes/dia na 106 (diagnostico de 18-19/08/2026).
SOCKET_TIMEOUT = 30
```

Em `client_worker`, substituir:
```python
    save_every = 1 if client_id == "105" else 10 # salva todas as imagens da 101 e 10% da 102 e 106(?)
    INTERVAL = 15 if client_id == "105" else 1
```
por:
```python
    save_every = 1 if client_id == "105" else 10  # 105 grava todo frame; 102/106, 1 a cada 10
```
e substituir:
```python
        client_socket.settimeout(INTERVAL)
```
por:
```python
        client_socket.settimeout(SOCKET_TIMEOUT)
```

- [ ] **Step 5: Rodar e ver passar** — `python -m pytest tests/test_servidor_patch.py -v` → PASS.

- [ ] **Step 6: Commit** — `git commit -m "fix(fase1): timeout do servidor 1s->30s, igual para todos os clientes (F2)"`

---

### Task 4: Fase 1.2 — systemd + NTP nas Rasps

**Files:**
- Create: `deploy/rasp/camera-client.service`, `deploy/rasp/porto-ntp.conf`, `deploy/rasp/install_camera_client.sh`

**Interfaces:**
- Produces: instalador idempotente rodado como root na Rasp: `sudo bash install_camera_client.sh` instala a unit (Restart=always), o NTP e habilita ambos. Assume o cliente em `/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/2026_02_05_Cliente_H01.py` (pendência da spec: confirmar com o time que a 105 pode migrar da variante `sem_grav_local` para a H01 antes de aplicar lá).

- [ ] **Step 1: Escrever os três arquivos**

`deploy/rasp/camera-client.service`:
```ini
[Unit]
Description=Cliente de camera Porto de Santos
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL
ExecStart=/usr/bin/python3 /home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/2026_02_05_Cliente_H01.py
Restart=always
RestartSec=10
# stdout/stderr vao para o journal: journalctl -u camera-client -f
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`deploy/rasp/porto-ntp.conf` (drop-in do systemd-timesyncd):
```ini
# /etc/systemd/timesyncd.conf.d/porto-ntp.conf
# NTP servido pelo PC do SPA (w32time), alcancado pela porta UDP 123
# encaminhada no CPE ELSYS do SPA.
[Time]
NTP=192.168.10.101
```

`deploy/rasp/install_camera_client.sh`:
```bash
#!/bin/bash
# Instala o cliente de camera como servico systemd + NTP interno.
# Uso: sudo bash install_camera_client.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT=/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/2026_02_05_Cliente_H01.py

[ -f "$CLIENT" ] || { echo "ERRO: $CLIENT nao existe nesta Rasp"; exit 1; }

echo "== unit systemd =="
cp "$HERE/camera-client.service" /etc/systemd/system/camera-client.service
systemctl daemon-reload
systemctl enable camera-client.service

echo "== NTP (systemd-timesyncd -> PC do SPA) =="
mkdir -p /etc/systemd/timesyncd.conf.d
cp "$HERE/porto-ntp.conf" /etc/systemd/timesyncd.conf.d/porto-ntp.conf
timedatectl set-ntp true
systemctl restart systemd-timesyncd

echo
echo "PRONTO. Agora, NA MAO (para nao matar uma transmissao em andamento):"
echo "  1. pare o cliente que roda em sessao manual (Ctrl+C no terminal/VNC)"
echo "  2. sudo systemctl start camera-client"
echo "  3. confira: systemctl status camera-client ; journalctl -u camera-client -f"
echo "  4. confira o relogio: timedatectl   (Synchronized deve virar yes em ~1 min)"
```

- [ ] **Step 2: Verificar sintaxe** — `bash -n deploy/rasp/install_camera_client.sh` → sem saída (ok).

- [ ] **Step 3: Commit** — `git commit -m "feat(fase1): unit systemd do cliente + NTP interno nas Rasps (F3, F5)"`

---

### Task 5: Fase 1.4 — NTP no PC + runbook de campo

**Files:**
- Create: `deploy/pc/enable_ntp_server.ps1`, `docs/runbooks/fase1-campo.md`

**Interfaces:**
- Produces: script PowerShell (admin) que ativa o servidor NTP do `w32time` e abre UDP 123 no firewall; runbook com a ordem exata de aplicação da Fase 1 inteira, incluindo os itens físicos (fonte da 106, bateria RTC) e o port-forward no CPE.

- [ ] **Step 1: Escrever `deploy/pc/enable_ntp_server.ps1`**

```powershell
# Ativa o servidor NTP nativo do Windows (w32time) para servir as Rasps.
# Rodar como Administrador no PC do SPA.
$ErrorActionPreference = "Stop"

# O PC sincroniza da internet (interface 192.168.124.29) e serve na 192.168.11.101
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpServer" -Name Enabled -Value 1
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config" -Name AnnounceFlags -Value 5
w32tm /config /manualpeerlist:"pool.ntp.org,0x9" /syncfromflags:manual /update

Set-Service w32time -StartupType Automatic
Restart-Service w32time

New-NetFirewallRule -DisplayName "NTP server (Rasps Porto)" -Direction Inbound `
  -Protocol UDP -LocalPort 123 -Action Allow -ErrorAction SilentlyContinue

w32tm /query /status
Write-Host "OK. Teste de uma Rasp:  ntpdate -q 192.168.10.101  (ou aguarde o timesyncd)"
```

- [ ] **Step 2: Verificar parse do PowerShell**

```powershell
[void][scriptblock]::Create((Get-Content -Raw deploy/pc/enable_ntp_server.ps1))
```
Expected: sem erro.

- [ ] **Step 3: Escrever `docs/runbooks/fase1-campo.md`**

```markdown
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
```

- [ ] **Step 4: Commit** — `git commit -m "feat(fase1): NTP no PC (w32time) + runbook de campo"`

---

### Task 6: `store.py` — série temporal em SQLite

**Files:**
- Create: `monitor/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `Store(db_path: Path)` thread-safe (lock interno, WAL):
  - `add_sample(ts: float, origin: str, metric: str, value: float) -> None`
  - `add_event(ts: float, origin: str, kind: str, detail: str) -> None`
  - `samples(origin: str, metric: str, since: float, until: float | None = None) -> list[tuple[float, float]]` (ordenado por ts)
  - `last_sample(origin: str, metric: str) -> tuple[float, float] | None`
  - `events(since: float, kind: str | None = None, origin: str | None = None) -> list[tuple[float, str, str, str]]` (ts, origin, kind, detail)
  - `count_events(origin: str, kind: str, since: float) -> int`
  - `purge(now: float, raw_days: int = 90) -> None` — agrega amostras velhas em `samples_hourly` (n/mean/min/max) e apaga o bruto
  - `close() -> None`

- [ ] **Step 1: Teste que falha**

`tests/test_store.py`:
```python
from monitor.store import Store

def test_samples_roundtrip(tmp_path):
    s = Store(tmp_path / "m.db")
    s.add_sample(100.0, "102", "frames_min", 6.0)
    s.add_sample(160.0, "102", "frames_min", 5.5)
    s.add_sample(160.0, "106", "frames_min", 2.0)
    assert s.samples("102", "frames_min", since=0) == [(100.0, 6.0), (160.0, 5.5)]
    assert s.last_sample("102", "frames_min") == (160.0, 5.5)
    assert s.last_sample("999", "frames_min") is None
    s.close()

def test_events_and_count(tmp_path):
    s = Store(tmp_path / "m.db")
    for i in range(6):
        s.add_event(100.0 + i, "106", "DISCONNECT", "timeout")
    s.add_event(200.0, "106", "CONNECT", "")
    assert s.count_events("106", "DISCONNECT", since=102.5) == 3
    kinds = [e[2] for e in s.events(since=0, origin="106")]
    assert kinds.count("DISCONNECT") == 6 and kinds.count("CONNECT") == 1
    s.close()

def test_purge_aggregates_old_samples(tmp_path):
    s = Store(tmp_path / "m.db")
    old = 1000.0
    for i in range(10):
        s.add_sample(old + i * 60, "102", "frames_min", float(i))
    now = old + 91 * 86400
    s.add_sample(now, "102", "frames_min", 9.9)
    s.purge(now, raw_days=90)
    assert s.samples("102", "frames_min", since=0) == [(now, 9.9)]
    hourly = s._conn.execute(
        "SELECT n, min, max FROM samples_hourly WHERE origin='102'").fetchall()
    assert hourly == [(10, 0.0, 9.0)]
    s.close()
```

- [ ] **Step 2: Rodar e ver falhar** — `python -m pytest tests/test_store.py -v` → FAIL.

- [ ] **Step 3: Implementar `monitor/store.py`**

```python
"""Serie temporal e eventos em SQLite (WAL), thread-safe por lock."""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples(ts REAL, origin TEXT, metric TEXT, value REAL);
CREATE INDEX IF NOT EXISTS ix_samples ON samples(origin, metric, ts);
CREATE TABLE IF NOT EXISTS events(ts REAL, origin TEXT, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_events ON events(origin, kind, ts);
CREATE TABLE IF NOT EXISTS samples_hourly(
  hour_ts REAL, origin TEXT, metric TEXT,
  n INTEGER, mean REAL, min REAL, max REAL,
  PRIMARY KEY(hour_ts, origin, metric));
"""


class Store:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def add_sample(self, ts, origin, metric, value):
        with self._lock:
            self._conn.execute("INSERT INTO samples VALUES(?,?,?,?)",
                               (ts, origin, metric, value))
            self._conn.commit()

    def add_event(self, ts, origin, kind, detail):
        with self._lock:
            self._conn.execute("INSERT INTO events VALUES(?,?,?,?)",
                               (ts, origin, kind, detail))
            self._conn.commit()

    def samples(self, origin, metric, since, until=None):
        q = "SELECT ts, value FROM samples WHERE origin=? AND metric=? AND ts>=?"
        args = [origin, metric, since]
        if until is not None:
            q += " AND ts<=?"
            args.append(until)
        with self._lock:
            return self._conn.execute(q + " ORDER BY ts", args).fetchall()

    def last_sample(self, origin, metric):
        with self._lock:
            row = self._conn.execute(
                "SELECT ts, value FROM samples WHERE origin=? AND metric=?"
                " ORDER BY ts DESC LIMIT 1", (origin, metric)).fetchone()
        return row

    def events(self, since, kind=None, origin=None):
        q = "SELECT ts, origin, kind, detail FROM events WHERE ts>=?"
        args = [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if origin:
            q += " AND origin=?"
            args.append(origin)
        with self._lock:
            return self._conn.execute(q + " ORDER BY ts", args).fetchall()

    def count_events(self, origin, kind, since):
        with self._lock:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE origin=? AND kind=? AND ts>=?",
                (origin, kind, since)).fetchone()
        return n

    def purge(self, now, raw_days=90):
        cutoff = now - raw_days * 86400
        with self._lock:
            self._conn.execute("""
                INSERT OR REPLACE INTO samples_hourly
                SELECT CAST(ts/3600 AS INTEGER)*3600.0, origin, metric,
                       COUNT(*), AVG(value), MIN(value), MAX(value)
                FROM samples WHERE ts < ?
                GROUP BY CAST(ts/3600 AS INTEGER), origin, metric""", (cutoff,))
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
```

- [ ] **Step 4: Rodar e ver passar** — `python -m pytest tests/test_store.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: store SQLite com retencao 90d + agregado horario"`

---

### Task 7: `taplog.py` — parser e seguidor do LOG_connections.txt

**Files:**
- Create: `monitor/taplog.py`
- Create: `tests/fixtures/log_connections_sample.txt`
- Test: `tests/test_taplog.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `LogEvent(ts: float, kind: str, client: str | None, detail: str)` — kind ∈ {"CONNECT", "DISCONNECT", "SERVER_START"}
  - `parse_line(line: str) -> LogEvent | None` (None para linha irreconhecível)
  - `LogFollower(path: Path, on_event: Callable[[LogEvent], None])` com `.poll() -> int` (nº de eventos novos; lê incrementalmente por offset; se o arquivo encolher, recomeça do zero; abre em modo leitura, `encoding="utf-8"`, `errors="replace"`)

Formatos REAIS (gerados por `rasp_101.py`):
```
2026-08-19 10:00:05 | CONNECT | client=102 | IP: 192.168.11.102 | total=1
2026-08-19 10:01:05 | DISCONNECT | client=106 | [106] Timeout, encerrando conexao... | total=0
SERVIDOR INICIADO!!   | 2026-08-19 09:59:00
```
(na linha de servidor o timestamp vem DEPOIS do texto)

- [ ] **Step 1: Fixture** — `tests/fixtures/log_connections_sample.txt` com exatamente:

```
SERVIDOR INICIADO!!   | 2026-08-19 09:59:00
2026-08-19 10:00:05 | CONNECT | client=102 | IP: 192.168.11.102 | total=1
2026-08-19 10:00:09 | CONNECT | client=106 | IP: 192.168.11.106 | total=2
2026-08-19 10:01:05 | DISCONNECT | client=106 | [106] Timeout, encerrando conexao... | total=1
linha corrompida sem formato nenhum
2026-08-19 10:01:20 | CONNECT | client=106 | IP: 192.168.11.106 | total=2
```

- [ ] **Step 2: Teste que falha**

`tests/test_taplog.py`:
```python
import shutil
from datetime import datetime
from pathlib import Path
from monitor.taplog import LogFollower, parse_line

FIX = Path("tests/fixtures/log_connections_sample.txt")

def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()

def test_parse_connect():
    ev = parse_line("2026-08-19 10:00:05 | CONNECT | client=102 | IP: 192.168.11.102 | total=1")
    assert ev.kind == "CONNECT" and ev.client == "102"
    assert ev.ts == _ts("2026-08-19 10:00:05")

def test_parse_disconnect_keeps_reason():
    ev = parse_line("2026-08-19 10:01:05 | DISCONNECT | client=106 | [106] Timeout, encerrando conexao... | total=1")
    assert ev.kind == "DISCONNECT" and ev.client == "106"
    assert "Timeout" in ev.detail

def test_parse_server_start_timestamp_at_end():
    ev = parse_line("SERVIDOR INICIADO!!   | 2026-08-19 09:59:00")
    assert ev.kind == "SERVER_START" and ev.client is None
    assert ev.ts == _ts("2026-08-19 09:59:00")

def test_parse_garbage_returns_none():
    assert parse_line("linha corrompida sem formato nenhum") is None
    assert parse_line("") is None

def test_follower_incremental_and_truncation(tmp_path):
    log = tmp_path / "log.txt"
    shutil.copy(FIX, log)
    got = []
    f = LogFollower(log, on_event=got.append)
    assert f.poll() == 5                      # 6 linhas, 1 lixo
    assert f.poll() == 0                      # nada novo
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("2026-08-19 10:05:00 | DISCONNECT | client=102 | [102] x | total=1\n")
    assert f.poll() == 1
    log.write_text("SERVIDOR INICIADO!!   | 2026-08-19 11:00:00\n", encoding="utf-8")
    assert f.poll() == 1                      # truncado -> relido do inicio
    assert got[-1].kind == "SERVER_START"
```

- [ ] **Step 3: Rodar e ver falhar**, depois implementar `monitor/taplog.py`:

```python
"""Parser e seguidor incremental do LOG_connections.txt (somente leitura)."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_TS = "%Y-%m-%d %H:%M:%S"
_EVENT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (CONNECT|DISCONNECT) \| "
    r"client=(\S+) \| (.*?) \| total=\d+\s*$")
_SERVER = re.compile(r"^SERVIDOR INICIADO!!\s*\| (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$")


@dataclass(frozen=True)
class LogEvent:
    ts: float
    kind: str            # CONNECT | DISCONNECT | SERVER_START
    client: Optional[str]
    detail: str


def parse_line(line: str) -> Optional[LogEvent]:
    m = _EVENT.match(line)
    if m:
        ts = datetime.strptime(m.group(1), _TS).timestamp()
        return LogEvent(ts, m.group(2), m.group(3), m.group(4))
    m = _SERVER.match(line)
    if m:
        ts = datetime.strptime(m.group(1), _TS).timestamp()
        return LogEvent(ts, "SERVER_START", None, "")
    return None


class LogFollower:
    def __init__(self, path: Path, on_event: Callable[[LogEvent], None]):
        self._path = Path(path)
        self._on_event = on_event
        self._offset = 0

    def poll(self) -> int:
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return 0
        if size < self._offset:          # truncado/rotacionado
            self._offset = 0
        if size == self._offset:
            return 0
        n = 0
        with open(self._path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._offset)
            for line in f:
                if not line.endswith("\n"):
                    break                 # linha ainda sendo escrita
                ev = parse_line(line.rstrip("\r\n"))
                if ev:
                    self._on_event(ev)
                    n += 1
                self._offset += len(line.encode("utf-8"))
        return n
```

- [ ] **Step 4: Rodar e ver passar** — `python -m pytest tests/test_taplog.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: parser e seguidor do LOG_connections.txt"`

---

### Task 8: `watcher.py` — frames/min a partir do disco

**Files:**
- Create: `monitor/watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `Config.cameras` (save_every), `Config.paths.images`.
- Produces:
  - `CameraSample(camera: str, frames_per_min: float | None, last_frame_age_s: float | None)` — `frames_per_min=None` no primeiro poll (sem janela ainda); `last_frame_age_s=None` se nunca houve arquivo (hoje nem ontem)
  - `FrameWatcher(images_dir: Path, cameras: dict[str, int])` com `.poll(now: float) -> list[CameraSample]`
- Regras: pasta do dia = `images_dir / time.strftime("%Y_%m_%d", localtime(now)) / camera`; taxa = (arquivos com mtime dentro da janela desde o último poll) × save_every × 60 ÷ janela; `last_frame_age_s` usa o maior mtime da pasta de hoje, com fallback na de ontem (virada de meia-noite); pasta inexistente conta 0 arquivos novos. Tudo via `os.scandir`, somente leitura.

- [ ] **Step 1: Teste que falha**

`tests/test_watcher.py`:
```python
import os
import time
from monitor.watcher import FrameWatcher

def _day(now):
    return time.strftime("%Y_%m_%d", time.localtime(now))

def _mkframe(root, day, cam, name, mtime):
    d = root / day / cam
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"x")
    os.utime(p, (mtime, mtime))

def test_first_poll_has_no_rate_but_has_age(tmp_path):
    now = time.time()
    _mkframe(tmp_path, _day(now), "102", "a.jpg", now - 30)
    w = FrameWatcher(tmp_path, {"102": 10})
    [s] = w.poll(now)
    assert s.frames_per_min is None
    assert 25 <= s.last_frame_age_s <= 35

def test_rate_corrected_by_save_every(tmp_path):
    now = time.time()
    w = FrameWatcher(tmp_path, {"102": 10})
    w.poll(now - 60)                          # abre a janela
    for i in range(6):                        # 6 gravados em 60s
        _mkframe(tmp_path, _day(now), "102", f"f{i}.jpg", now - 50 + i * 8)
    [s] = w.poll(now)
    # 6 arquivos * save_every 10 / 1 min = 60 frames/min
    assert abs(s.frames_per_min - 60.0) < 1.0

def test_missing_today_falls_back_to_yesterday(tmp_path):
    now = time.time()
    yesterday = now - 86400
    _mkframe(tmp_path, _day(yesterday), "106", "old.jpg", yesterday)
    w = FrameWatcher(tmp_path, {"106": 10})
    w.poll(now - 60)
    [s] = w.poll(now)
    assert s.frames_per_min == 0.0
    assert s.last_frame_age_s >= 86000

def test_never_any_frame(tmp_path):
    now = time.time()
    w = FrameWatcher(tmp_path, {"105": 1})
    w.poll(now - 60)
    [s] = w.poll(now)
    assert s.frames_per_min == 0.0 and s.last_frame_age_s is None
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar `monitor/watcher.py`:

```python
"""Taxa de entrega por camera, observando D:\\SPA_Data por mtime (somente leitura)."""
from __future__ import annotations
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CameraSample:
    camera: str
    frames_per_min: Optional[float]
    last_frame_age_s: Optional[float]


def _scan(day_dir: Path, since: float) -> tuple[int, Optional[float]]:
    """(arquivos com mtime > since, maior mtime) — 0/None se a pasta nao existe."""
    count, newest = 0, None
    try:
        with os.scandir(day_dir) as it:
            for e in it:
                if not e.is_file():
                    continue
                m = e.stat().st_mtime
                if m > since:
                    count += 1
                if newest is None or m > newest:
                    newest = m
    except OSError:
        pass
    return count, newest


class FrameWatcher:
    def __init__(self, images_dir: Path, cameras: dict[str, int]):
        self._root = Path(images_dir)
        self._cameras = dict(cameras)
        self._last_poll: Optional[float] = None

    def _day_dir(self, camera: str, ts: float) -> Path:
        return self._root / time.strftime("%Y_%m_%d", time.localtime(ts)) / camera

    def poll(self, now: float) -> list[CameraSample]:
        out = []
        window = None if self._last_poll is None else now - self._last_poll
        for cam, save_every in self._cameras.items():
            since = self._last_poll if self._last_poll is not None else now
            n_today, newest = _scan(self._day_dir(cam, now), since)
            n_yest, newest_y = _scan(self._day_dir(cam, now - 86400), since)
            newest_any = max((m for m in (newest, newest_y) if m is not None),
                             default=None)
            rate = None
            if window and window > 0:
                rate = (n_today + n_yest) * save_every * 60.0 / window
            age = (now - newest_any) if newest_any is not None else None
            out.append(CameraSample(cam, rate, age))
        self._last_poll = now
        return out
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_watcher.py -v` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "feat: watcher de frames/min com correcao por save_every"`

---

### Task 9: `baseline.py` — linha de base por hora do dia

**Files:**
- Create: `monitor/baseline.py`
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `Store.samples(origin, "frames_min", since)`.
- Produces: `hourly_baseline(store, camera: str, hour: int, now: float, days: int = 7, min_samples: int = 20) -> float | None` — mediana das amostras `frames_min` da câmera caídas naquela hora local do dia nos últimos `days` dias; `None` com menos de `min_samples` (alerta de degradação é pulado sem linha de base).

- [ ] **Step 1: Teste que falha**

`tests/test_baseline.py`:
```python
from datetime import datetime
from monitor.baseline import hourly_baseline
from monitor.store import Store

def _at(day, hour):
    return datetime(2026, 8, day, hour, 30).timestamp()

def test_median_of_matching_hour_only(tmp_path):
    s = Store(tmp_path / "m.db")
    for day in range(10, 17):                       # 7 dias
        for i, v in enumerate([50.0, 55.0, 60.0]):  # 3 amostras na hora 14
            s.add_sample(_at(day, 14) + i * 60, "102", "frames_min", v)
        s.add_sample(_at(day, 3), "102", "frames_min", 5.0)  # madrugada != hora 14
    now = _at(17, 15)
    assert hourly_baseline(s, "102", 14, now) == 55.0
    s.close()

def test_none_when_insufficient(tmp_path):
    s = Store(tmp_path / "m.db")
    for i in range(5):
        s.add_sample(_at(16, 14) + i * 60, "102", "frames_min", 50.0)
    assert hourly_baseline(s, "102", 14, _at(17, 15)) is None
    s.close()
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar `monitor/baseline.py`:

```python
"""Linha de base por camera e hora do dia: mediana de 7 dias."""
from __future__ import annotations
import statistics
from datetime import datetime
from typing import Optional

from monitor.store import Store


def hourly_baseline(store: Store, camera: str, hour: int, now: float,
                    days: int = 7, min_samples: int = 20) -> Optional[float]:
    vals = [v for ts, v in store.samples(camera, "frames_min", now - days * 86400)
            if datetime.fromtimestamp(ts).hour == hour]
    if len(vals) < min_samples:
        return None
    return statistics.median(vals)
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_baseline.py -v` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "feat: linha de base por hora do dia (mediana 7d)"`

---

### Task 10: `alerts.py` — máquina de estados

**Files:**
- Create: `monitor/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `AlertCfg` (Task 1).
- Produces:
  - `CamState(frames_per_min: float | None, last_frame_age_s: float | None, baseline: float | None, disconnects_15min: int)`
  - `Snapshot(cameras: dict[str, CamState], disk_free_gb: float, rsrp: float | None, rsrq: float | None)`
  - `Alert(kind: str, origin: str, text: str, wants_chart: bool)` — kind ∈ {"down", "degraded", "flapping", "link", "disk", "recovered", "group_down"}
  - `AlertEngine(cfg: AlertCfg)` com `.evaluate(now: float, snap: Snapshot) -> list[Alert]` — puro exceto pelo estado interno (supressão e persistência); avaliador chamado a cada ~30 s pelo service.
- Regras (valores da spec, lidos do cfg):
  - **down**: `last_frame_age_s >= down_after_s` (age None = sem dado → não dispara);
  - **recovered**: estava down e `last_frame_age_s < 60`;
  - **group_down**: ≥2 câmeras transicionam para down na MESMA avaliação → um único alerta (e não os individuais);
  - **degraded**: baseline existe e `frames_per_min < degraded_ratio × baseline` contínuo por `degraded_after_s` (não dispara se down);
  - **flapping**: `disconnects_15min >= flap_count`;
  - **link**: `rsrp < link_rsrp_min` ou `rsrq < link_rsrq_min` contínuo por `link_after_s`; rsrp/rsrq None → sem avaliação;
  - **disk**: `disk_free_gb < disk_min_gb`, re-alerta a cada 24 h;
  - supressão geral: mesmo (origin, kind) só re-envia após `realert_s`, exceto se houve `recovered` no meio.

- [ ] **Step 1: Teste que falha**

`tests/test_alerts.py`:
```python
from monitor.alerts import AlertEngine, CamState, Snapshot
from monitor.config import AlertCfg

CFG = AlertCfg(disk_min_gb=60.0, down_after_s=180, degraded_ratio=0.5,
               degraded_after_s=600, flap_count=5, flap_window_s=900,
               realert_s=1800, link_rsrp_min=-110.0, link_rsrq_min=-15.0,
               link_after_s=300)

def cam(age=10.0, rate=60.0, base=60.0, disc=0):
    return CamState(frames_per_min=rate, last_frame_age_s=age,
                    baseline=base, disconnects_15min=disc)

def snap(cams, disk=200.0, rsrp=None, rsrq=None):
    return Snapshot(cameras=cams, disk_free_gb=disk, rsrp=rsrp, rsrq=rsrq)

def kinds(alerts):
    return [(a.kind, a.origin) for a in alerts]

def test_down_fires_once_then_recovers_then_refires():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"102": cam(age=200)}))) == [("down", "102")]
    assert e.evaluate(1030, snap({"102": cam(age=230)})) == []          # suprimido
    assert kinds(e.evaluate(1100, snap({"102": cam(age=5)}))) == [("recovered", "102")]
    assert kinds(e.evaluate(1200, snap({"102": cam(age=300)}))) == [("down", "102")]

def test_group_down_replaces_individuals():
    e = AlertEngine(CFG)
    s = snap({"102": cam(age=200), "106": cam(age=210), "105": cam(age=5)})
    out = e.evaluate(1000, s)
    assert kinds(out) == [("group_down", "*")]
    assert "102" in out[0].text and "106" in out[0].text

def test_degraded_needs_persistence_and_baseline():
    e = AlertEngine(CFG)
    low = {"102": cam(age=5, rate=20.0, base=60.0)}
    assert e.evaluate(1000, snap(low)) == []                 # comecou a contar
    assert e.evaluate(1000 + 599, snap(low)) == []           # ainda nao
    assert kinds(e.evaluate(1000 + 601, snap(low))) == [("degraded", "102")]
    e2 = AlertEngine(CFG)
    nobase = {"102": cam(age=5, rate=20.0, base=None)}
    assert e2.evaluate(1000, snap(nobase)) == []
    assert e2.evaluate(2000, snap(nobase)) == []

def test_flapping():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"106": cam(disc=5)}))) == [("flapping", "106")]
    assert e.evaluate(1030, snap({"106": cam(disc=6)})) == []  # suprimido 30min

def test_link_needs_persistence():
    e = AlertEngine(CFG)
    s = snap({"102": cam()}, rsrp=-115.0, rsrq=-6.0)
    assert e.evaluate(1000, s) == []
    assert kinds(e.evaluate(1000 + 301, s)) == [("link", "cpe")]

def test_disk_realerts_daily_not_halfhourly():
    e = AlertEngine(CFG)
    s = snap({"102": cam()}, disk=50.0)
    assert kinds(e.evaluate(1000, s)) == [("disk", "pc")]
    assert e.evaluate(1000 + 7200, s) == []                   # 2h: nada
    assert kinds(e.evaluate(1000 + 86401, s)) == [("disk", "pc")]
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar `monitor/alerts.py`:

```python
"""Maquina de estados dos alertas. evaluate() e chamada a cada ~30s."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from monitor.config import AlertCfg


@dataclass(frozen=True)
class CamState:
    frames_per_min: Optional[float]
    last_frame_age_s: Optional[float]
    baseline: Optional[float]
    disconnects_15min: int


@dataclass(frozen=True)
class Snapshot:
    cameras: dict[str, CamState]
    disk_free_gb: float
    rsrp: Optional[float]
    rsrq: Optional[float]


@dataclass(frozen=True)
class Alert:
    kind: str
    origin: str
    text: str
    wants_chart: bool = False


class AlertEngine:
    def __init__(self, cfg: AlertCfg):
        self.cfg = cfg
        self._down: set[str] = set()
        self._deg_since: dict[str, float] = {}
        self._link_bad_since: Optional[float] = None
        self._last_sent: dict[tuple[str, str], float] = {}

    # -- supressao -------------------------------------------------------
    def _ok_to_send(self, now, origin, kind, interval=None) -> bool:
        last = self._last_sent.get((origin, kind))
        if last is not None and now - last < (interval or self.cfg.realert_s):
            return False
        self._last_sent[(origin, kind)] = now
        return True

    def _clear(self, origin, kind):
        self._last_sent.pop((origin, kind), None)

    # -- avaliacao -------------------------------------------------------
    def evaluate(self, now: float, snap: Snapshot) -> list[Alert]:
        out: list[Alert] = []
        newly_down: list[str] = []

        for cam, st in snap.cameras.items():
            age = st.last_frame_age_s
            is_down = age is not None and age >= self.cfg.down_after_s

            if is_down and cam not in self._down:
                self._down.add(cam)
                newly_down.append(cam)
            elif not is_down and cam in self._down and age is not None and age < 60:
                self._down.discard(cam)
                self._clear(cam, "down")
                if self._ok_to_send(now, cam, "recovered", interval=1):
                    out.append(Alert("recovered", cam,
                                     f"Camera {cam} voltou a enviar frames.", False))

            # degradada (nunca junto com down)
            if (not is_down and st.baseline is not None
                    and st.frames_per_min is not None
                    and st.frames_per_min < self.cfg.degraded_ratio * st.baseline):
                self._deg_since.setdefault(cam, now)
                if (now - self._deg_since[cam] > self.cfg.degraded_after_s
                        and self._ok_to_send(now, cam, "degraded")):
                    out.append(Alert(
                        "degraded", cam,
                        f"Camera {cam} degradada: {st.frames_per_min:.1f} frames/min "
                        f"(linha de base da hora: {st.baseline:.1f}).", True))
            else:
                self._deg_since.pop(cam, None)

            if (st.disconnects_15min >= self.cfg.flap_count
                    and self._ok_to_send(now, cam, "flapping")):
                out.append(Alert(
                    "flapping", cam,
                    f"Camera {cam}: {st.disconnects_15min} desconexoes em 15 min.", True))

        # down individual ou agrupado
        if len(newly_down) >= 2:
            names = ", ".join(sorted(newly_down))
            for cam in newly_down:
                self._last_sent[(cam, "down")] = now
            out.append(Alert("group_down", "*",
                             f"{len(newly_down)} cameras sem frames ao mesmo tempo "
                             f"({names}) - provavel enlace/eNodeB.", True))
        else:
            for cam in newly_down:
                if self._ok_to_send(now, cam, "down"):
                    st = snap.cameras[cam]
                    age_min = (st.last_frame_age_s or 0) / 60
                    out.append(Alert("down", cam,
                                     f"Camera {cam} SEM FRAMES ha {age_min:.0f} min.", True))

        # enlace
        bad = ((snap.rsrp is not None and snap.rsrp < self.cfg.link_rsrp_min)
               or (snap.rsrq is not None and snap.rsrq < self.cfg.link_rsrq_min))
        if bad:
            if self._link_bad_since is None:
                self._link_bad_since = now
            elif (now - self._link_bad_since > self.cfg.link_after_s
                  and self._ok_to_send(now, "cpe", "link")):
                out.append(Alert("link", "cpe",
                                 f"Enlace ruim: RSRP={snap.rsrp} dBm, RSRQ={snap.rsrq} dB.",
                                 False))
        else:
            self._link_bad_since = None

        # disco (re-alerta diario)
        if snap.disk_free_gb < self.cfg.disk_min_gb:
            if self._ok_to_send(now, "pc", "disk", interval=86400):
                out.append(Alert("disk", "pc",
                                 f"Disco D: com {snap.disk_free_gb:.0f} GB livres "
                                 f"(minimo {self.cfg.disk_min_gb:.0f} GB).", False))
        else:
            self._clear("pc", "disk")

        return out
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_alerts.py -v` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "feat: maquina de estados de alertas com anti-ruido"`

---

### Task 11: `telegram.py` + `charts.py`

**Files:**
- Create: `monitor/telegram.py`, `monitor/charts.py`
- Test: `tests/test_telegram.py`, `tests/test_charts.py`

**Interfaces:**
- Consumes: `TelegramCfg`; `Store` (charts).
- Produces:
  - `TelegramClient(cfg: TelegramCfg, data_dir: Path, post: Callable | None = None)` — `post(url, data, files) -> bool` injetável para teste; default usa `requests.post` com timeout 15 s.
    - `.send_text(text: str) -> bool`
    - `.send_photo(png: bytes, caption: str) -> bool`
    - `.flush_outbox() -> int` — reenviou N mensagens de texto pendentes (falhas de `send_text` vão para `data_dir/outbox.jsonl` com o carimbo original; fotos NÃO são enfileiradas — só a legenda, como texto)
    - `.get_updates(offset: int) -> list[dict]` — long-poll 25 s (para o bot, Task 13)
  - `charts.render_camera_chart(store: Store, camera: str, now: float, window_s: int = 1800) -> bytes` — PNG: frames/min (eixo 1), RSRP (eixo 2 se houver amostras `origin="cpe", metric="rsrp"`), marcadores verticais nos eventos DISCONNECT da câmera.

- [ ] **Step 1: Testes que falham**

`tests/test_telegram.py`:
```python
import json
from monitor.config import TelegramCfg
from monitor.telegram import TelegramClient

CFG = TelegramCfg(enabled=True, token="T", chat_id="C")

def test_send_text_posts_to_api(tmp_path):
    calls = []
    tc = TelegramClient(CFG, tmp_path, post=lambda url, data, files=None: calls.append((url, data)) or True)
    assert tc.send_text("oi") is True
    url, data = calls[0]
    assert "botT/sendMessage" in url and data["chat_id"] == "C" and data["text"] == "oi"

def test_failed_text_goes_to_outbox_and_flushes(tmp_path):
    ok = {"v": False}
    tc = TelegramClient(CFG, tmp_path, post=lambda *a, **k: ok["v"])
    assert tc.send_text("perdida") is False
    outbox = tmp_path / "outbox.jsonl"
    assert json.loads(outbox.read_text().splitlines()[0])["text"] == "perdida"
    ok["v"] = True
    assert tc.flush_outbox() == 1
    assert outbox.read_text().strip() == ""

def test_send_photo_uses_files(tmp_path):
    calls = []
    tc = TelegramClient(CFG, tmp_path,
                        post=lambda url, data, files=None: calls.append((url, files)) or True)
    assert tc.send_photo(b"\x89PNG...", "grafico") is True
    url, files = calls[0]
    assert "sendPhoto" in url and files["photo"][1] == b"\x89PNG..."
```

`tests/test_charts.py`:
```python
from monitor.charts import render_camera_chart
from monitor.store import Store

def test_renders_png_with_data_and_events(tmp_path):
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    for i in range(30):
        s.add_sample(now - 1800 + i * 60, "106", "frames_min", 30.0 + i)
        s.add_sample(now - 1800 + i * 60, "cpe", "rsrp", -95.0)
    s.add_event(now - 900, "106", "DISCONNECT", "timeout")
    png = render_camera_chart(s, "106", now)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    s.close()

def test_renders_even_with_empty_store(tmp_path):
    s = Store(tmp_path / "m.db")
    png = render_camera_chart(s, "102", 1_700_000_000.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    s.close()
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar.

`monitor/telegram.py`:
```python
"""Cliente minimo da Bot API do Telegram, com outbox em disco para texto."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Callable, Optional

from monitor.config import TelegramCfg


def _default_post(url, data, files=None) -> bool:
    import requests
    try:
        r = requests.post(url, data=data, files=files, timeout=15)
        return r.ok
    except Exception:
        return False


class TelegramClient:
    def __init__(self, cfg: TelegramCfg, data_dir: Path,
                 post: Optional[Callable] = None):
        self.cfg = cfg
        self._post = post or _default_post
        self._outbox = Path(data_dir) / "outbox.jsonl"
        self._outbox.parent.mkdir(parents=True, exist_ok=True)

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.cfg.token}/{method}"

    def send_text(self, text: str) -> bool:
        ok = self._post(self._api("sendMessage"),
                        {"chat_id": self.cfg.chat_id, "text": text})
        if not ok:
            with open(self._outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "text": text}) + "\n")
        return ok

    def send_photo(self, png: bytes, caption: str) -> bool:
        ok = self._post(self._api("sendPhoto"),
                        {"chat_id": self.cfg.chat_id, "caption": caption},
                        files={"photo": ("chart.png", png, "image/png")})
        if not ok:
            self.send_text(caption + " [grafico indisponivel na hora do envio]")
        return ok

    def flush_outbox(self) -> int:
        if not self._outbox.exists():
            return 0
        lines = [l for l in self._outbox.read_text(encoding="utf-8").splitlines() if l]
        sent = 0
        rest = []
        for line in lines:
            msg = json.loads(line)
            stamp = time.strftime("%d/%m %H:%M", time.localtime(msg["ts"]))
            if self._post(self._api("sendMessage"),
                          {"chat_id": self.cfg.chat_id,
                           "text": f"[atrasada, de {stamp}] {msg['text']}"}):
                sent += 1
            else:
                rest.append(line)
        self._outbox.write_text("\n".join(rest) + ("\n" if rest else ""),
                                encoding="utf-8")
        return sent

    def get_updates(self, offset: int) -> list[dict]:
        import requests
        try:
            r = requests.get(self._api("getUpdates"),
                             params={"offset": offset, "timeout": 25}, timeout=35)
            return r.json().get("result", []) if r.ok else []
        except Exception:
            return []
```

`monitor/charts.py`:
```python
"""Graficos PNG para os alertas e o comando /grafico."""
from __future__ import annotations
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from monitor.store import Store


def render_camera_chart(store: Store, camera: str, now: float,
                        window_s: int = 1800) -> bytes:
    since = now - window_s
    frames = store.samples(camera, "frames_min", since)
    rsrp = store.samples("cpe", "rsrp", since)
    disc = [e[0] for e in store.events(since, kind="DISCONNECT", origin=camera)]

    fig, ax1 = plt.subplots(figsize=(8, 4), dpi=110)
    title_win = window_s // 60
    ax1.set_title(f"Camera {camera} - ultimos {title_win} min")
    if frames:
        xs = [datetime.fromtimestamp(t) for t, _ in frames]
        ax1.plot(xs, [v for _, v in frames], lw=1.8, label="frames/min")
    ax1.set_ylabel("frames/min")
    ax1.set_ylim(bottom=0)
    for d in disc:
        ax1.axvline(datetime.fromtimestamp(d), ls="--", lw=0.8, alpha=0.6, color="red")
    if rsrp:
        ax2 = ax1.twinx()
        xs = [datetime.fromtimestamp(t) for t, _ in rsrp]
        ax2.plot(xs, [v for _, v in rsrp], lw=1.2, alpha=0.7, color="gray",
                 label="RSRP (dBm)")
        ax2.set_ylabel("RSRP (dBm)")
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_telegram.py tests/test_charts.py -v` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "feat: cliente Telegram com outbox + graficos matplotlib"`

---

### Task 12: `cpe.py` — scraper do Amplimax + CLI de sondagem

**Files:**
- Create: `monitor/cpe.py`
- Test: `tests/test_cpe.py`

**Interfaces:**
- Consumes: `CpeCfg` (regex configuráveis — a página real ainda não foi capturada; pendência da spec, calibrar em campo com `--probe`).
- Produces:
  - `CpeReading(rsrp: float | None, rsrq: float | None, connected: bool)`
  - `CpeScraper(cfg: CpeCfg, get: Callable | None = None)` — `get(url, auth) -> str | None` injetável; default `requests.get` com basic auth se username preenchido, timeout 10 s.
    - `.fetch() -> CpeReading | None` — None quando o HTTP falhou (coletor grava `cpe_up=0`)
  - CLI: `python -m monitor.cpe --probe [--config config.toml]` imprime o HTML bruto e o que os regexes capturaram — usado uma vez em campo para calibrar `rsrp_re`/`rsrq_re` e então ligar `cpe.enabled`.

- [ ] **Step 1: Teste que falha**

`tests/test_cpe.py`:
```python
from monitor.config import CpeCfg
from monitor.cpe import CpeScraper

CFG = CpeCfg(enabled=True, url="http://cpe/", username="", password="",
             rsrp_re=r'(-?\d+)\s*dBm\s*\(RSRP\)', rsrq_re=r'(-?\d+)\s*dB\s*\(RSRQ\)',
             interval_s=60)

HTML = "<td>-91 dBm (RSRP)</td><td>-6 dB (RSRQ)</td><td>Conectado</td>"

def test_parses_rsrp_rsrq_connected():
    sc = CpeScraper(CFG, get=lambda url, auth: HTML)
    r = sc.fetch()
    assert r.rsrp == -91.0 and r.rsrq == -6.0 and r.connected is True

def test_http_failure_returns_none():
    sc = CpeScraper(CFG, get=lambda url, auth: None)
    assert sc.fetch() is None

def test_page_without_metrics_still_reports():
    sc = CpeScraper(CFG, get=lambda url, auth: "<html>login</html>")
    r = sc.fetch()
    assert r.rsrp is None and r.rsrq is None and r.connected is False
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar `monitor/cpe.py`:

```python
"""Scraper do web UI do ELSYS Amplimax. Regexes vem do config: a pagina real
ainda nao foi capturada -- calibrar em campo com `python -m monitor.cpe --probe`."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable, Optional

from monitor.config import CpeCfg


@dataclass(frozen=True)
class CpeReading:
    rsrp: Optional[float]
    rsrq: Optional[float]
    connected: bool


def _default_get(url: str, auth) -> Optional[str]:
    import requests
    try:
        r = requests.get(url, auth=auth, timeout=10)
        return r.text if r.ok else None
    except Exception:
        return None


class CpeScraper:
    def __init__(self, cfg: CpeCfg, get: Optional[Callable] = None):
        self.cfg = cfg
        self._get = get or _default_get

    def fetch(self) -> Optional[CpeReading]:
        auth = (self.cfg.username, self.cfg.password) if self.cfg.username else None
        html = self._get(self.cfg.url, auth)
        if html is None:
            return None
        rsrp = _first_float(self.cfg.rsrp_re, html)
        rsrq = _first_float(self.cfg.rsrq_re, html)
        return CpeReading(rsrp, rsrq, "Conectado" in html)


def _first_float(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    from monitor.config import load_config

    ap = argparse.ArgumentParser(description="Sondagem do CPE para calibrar os regexes")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--config", default="config.toml")
    args = ap.parse_args()
    cfg = load_config(Path(args.config)).cpe
    auth = (cfg.username, cfg.password) if cfg.username else None
    html = _default_get(cfg.url, auth)
    if html is None:
        print("FALHA: sem resposta HTTP de", cfg.url)
    else:
        print(html)
        print("=" * 60)
        print("rsrp_re capturou:", _first_float(cfg.rsrp_re, html))
        print("rsrq_re capturou:", _first_float(cfg.rsrq_re, html))
        print("'Conectado' presente:", "Conectado" in html)
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_cpe.py -v` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "feat: scraper do CPE com regex configuravel e CLI de sondagem"`

---

### Task 13: `bot.py` + `panel.py` + `summary.py`

**Files:**
- Create: `monitor/bot.py`, `monitor/panel.py`, `monitor/summary.py`
- Test: `tests/test_bot.py`, `tests/test_panel.py`, `tests/test_summary.py`

**Interfaces:**
- Consumes: `Store`, `Config`, `render_camera_chart` (Task 11), `hourly_baseline` (Task 9).
- Produces:
  - `bot.BotHandler(store: Store, cfg: Config)` com `.handle(text: str, now: float) -> tuple[str, bytes | None]` — (resposta, PNG opcional). Comandos: `/status` e `/grafico <id>`; resto responde ajuda. O loop getUpdates fica no service (Task 14).
  - `panel.build_status(store: Store, cfg: Config, now: float) -> dict` — `{"cameras": {id: {"state": "ok|atrasada|sem_dados", "frames_min": float|None, "last_frame_age_s": float|None, "disconnects_24h": int}}, "disk_free_gb": float|None, "rsrp": float|None, "cpe_up": bool|None}` (estado: `sem_dados` sem amostra; `atrasada` com age > 180 s; senão `ok`; disk/rsrp lidos de `last_sample("pc","disk_free_gb")` / `("cpe","rsrp")`)
  - `panel.render_html(status: dict) -> str` — página única com meta refresh 30 s
  - `panel.run_panel(store, cfg)` — `ThreadingHTTPServer` na porta do cfg servindo `/` (HTML) e `/api/status` (JSON); bloqueante (o service roda em thread)
  - `summary.build_daily_summary(store: Store, cfg: Config, now: float) -> str` — texto com, por câmera: nº DISCONNECT nas últimas 24 h, disponibilidade % (fração dos samples `frames_min` > 0), pior hora (hora local com mais DISCONNECTs); + disco livre e tendência (GB/dia sobre os `disk_free_gb` de 7 dias).

- [ ] **Step 1: Testes que falham**

`tests/test_summary.py`:
```python
from monitor.summary import build_daily_summary
from monitor.config import load_config
from monitor.store import Store
from pathlib import Path

def test_summary_counts_disconnects_and_availability(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    for i in range(24 * 6):                       # amostra a cada 10 min
        s.add_sample(now - 86400 + i * 600, "102", "frames_min",
                     0.0 if i < 12 else 50.0)     # 2h fora do ar
    for i in range(7):
        s.add_event(now - 3600 * i, "106", "DISCONNECT", "timeout")
    s.add_sample(now, "pc", "disk_free_gb", 250.0)
    text = build_daily_summary(s, cfg, now)
    assert "106: 7 quedas" in text
    assert "102" in text and "%" in text
    assert "250" in text
    s.close()
```

`tests/test_bot.py`:
```python
from pathlib import Path
from monitor.bot import BotHandler
from monitor.config import load_config
from monitor.store import Store

def _mk(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    s.add_sample(now - 30, "102", "frames_min", 55.0)
    return BotHandler(s, cfg), now, s

def test_status_lists_cameras(tmp_path):
    bot, now, s = _mk(tmp_path)
    text, png = bot.handle("/status", now)
    assert png is None
    for cam in ("102", "105", "106"):
        assert cam in text
    s.close()

def test_grafico_returns_png(tmp_path):
    bot, now, s = _mk(tmp_path)
    text, png = bot.handle("/grafico 102", now)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    s.close()

def test_unknown_command_gets_help(tmp_path):
    bot, now, s = _mk(tmp_path)
    text, png = bot.handle("/xyz", now)
    assert "/status" in text and png is None
    s.close()
```

`tests/test_panel.py`:
```python
from pathlib import Path
from monitor.config import load_config
from monitor.panel import build_status, render_html
from monitor.store import Store

def test_build_status_states(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    s.add_sample(now - 30, "102", "frames_min", 55.0)
    s.add_sample(now - 30, "102", "last_frame_age_s", 10.0)
    s.add_sample(now - 30, "106", "last_frame_age_s", 400.0)
    st = build_status(s, cfg, now)
    assert st["cameras"]["102"]["state"] == "ok"
    assert st["cameras"]["106"]["state"] == "atrasada"
    assert st["cameras"]["105"]["state"] == "sem_dados"
    s.close()

def test_render_html_contains_cameras(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    html = render_html(build_status(s, cfg, 1_700_000_000.0))
    assert "102" in html and "105" in html and "106" in html
    assert 'http-equiv="refresh"' in html
    s.close()
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar.

`monitor/summary.py`:
```python
"""Resumo diario das 08:00."""
from __future__ import annotations
import time

from monitor.config import Config
from monitor.store import Store


def build_daily_summary(store: Store, cfg: Config, now: float) -> str:
    since = now - 86400
    lines = ["Resumo diario - cameras Porto de Santos", ""]
    for cam in sorted(cfg.cameras):
        n_disc = store.count_events(cam, "DISCONNECT", since)
        samples = store.samples(cam, "frames_min", since)
        if samples:
            avail = 100.0 * sum(1 for _, v in samples if v > 0) / len(samples)
            avail_s = f"{avail:.0f}% do dia com frames"
        else:
            avail_s = "sem dados"
        worst = _worst_hour(store, cam, since)
        worst_s = f", pior hora {worst}h" if worst is not None else ""
        lines.append(f"- {cam}: {n_disc} quedas, {avail_s}{worst_s}")
    disk = store.last_sample("pc", "disk_free_gb")
    if disk:
        trend = _disk_trend(store, now)
        t = f" ({trend:+.1f} GB/dia)" if trend is not None else ""
        lines += ["", f"Disco D: {disk[1]:.0f} GB livres{t}"]
    return "\n".join(lines)


def _worst_hour(store, cam, since):
    hours = [time.localtime(e[0]).tm_hour
             for e in store.events(since, kind="DISCONNECT", origin=cam)]
    return max(set(hours), key=hours.count) if hours else None


def _disk_trend(store, now):
    pts = store.samples("pc", "disk_free_gb", now - 7 * 86400)
    if len(pts) < 2:
        return None
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    days = (t1 - t0) / 86400
    return (v1 - v0) / days if days > 0.5 else None
```

`monitor/bot.py`:
```python
"""Comandos do bot: /status e /grafico <id>."""
from __future__ import annotations
from typing import Optional

from monitor.charts import render_camera_chart
from monitor.config import Config
from monitor.panel import build_status
from monitor.store import Store

_HELP = ("Comandos:\n/status - estado atual das cameras\n"
         "/grafico <id> - ultimas 24h da camera (ex.: /grafico 102)")


class BotHandler:
    def __init__(self, store: Store, cfg: Config):
        self.store = store
        self.cfg = cfg

    def handle(self, text: str, now: float) -> tuple[str, Optional[bytes]]:
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""
        if cmd == "/status":
            return self._status(now), None
        if cmd == "/grafico" and len(parts) > 1 and parts[1] in self.cfg.cameras:
            png = render_camera_chart(self.store, parts[1], now, window_s=86400)
            return f"Camera {parts[1]} - ultimas 24h", png
        return _HELP, None

    def _status(self, now: float) -> str:
        st = build_status(self.store, self.cfg, now)
        icon = {"ok": "OK", "atrasada": "ATRASADA", "sem_dados": "SEM DADOS"}
        lines = ["Estado atual:"]
        for cam, c in sorted(st["cameras"].items()):
            fpm = f'{c["frames_min"]:.1f} f/min' if c["frames_min"] is not None else "-"
            age = (f'{c["last_frame_age_s"]:.0f}s atras'
                   if c["last_frame_age_s"] is not None else "-")
            lines.append(f'- {cam}: {icon[c["state"]]} | {fpm} | ultimo frame {age} '
                         f'| {c["disconnects_24h"]} quedas 24h')
        if st["disk_free_gb"] is not None:
            lines.append(f'Disco D: {st["disk_free_gb"]:.0f} GB livres')
        if st["rsrp"] is not None:
            lines.append(f'Enlace: RSRP {st["rsrp"]:.0f} dBm')
        return "\n".join(lines)
```

`monitor/panel.py`:
```python
"""Painel local somente leitura: / (HTML, refresh 30s) e /api/status (JSON)."""
from __future__ import annotations
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from monitor.config import Config
from monitor.store import Store


def build_status(store: Store, cfg: Config, now: float) -> dict:
    cams = {}
    for cam in cfg.cameras:
        fpm = store.last_sample(cam, "frames_min")
        age = store.last_sample(cam, "last_frame_age_s")
        if age is None:
            state = "sem_dados"
        elif age[1] > 180:
            state = "atrasada"
        else:
            state = "ok"
        cams[cam] = {
            "state": state,
            "frames_min": fpm[1] if fpm else None,
            "last_frame_age_s": age[1] if age else None,
            "disconnects_24h": store.count_events(cam, "DISCONNECT", now - 86400),
        }
    disk = store.last_sample("pc", "disk_free_gb")
    rsrp = store.last_sample("cpe", "rsrp")
    up = store.last_sample("cpe", "cpe_up")
    return {"cameras": cams,
            "disk_free_gb": disk[1] if disk else None,
            "rsrp": rsrp[1] if rsrp else None,
            "cpe_up": bool(up[1]) if up else None}


_COLORS = {"ok": "#2f9e44", "atrasada": "#e8590c", "sem_dados": "#868e96"}


def render_html(status: dict) -> str:
    rows = []
    for cam, c in sorted(status["cameras"].items()):
        color = _COLORS[c["state"]]
        fpm = f'{c["frames_min"]:.1f}' if c["frames_min"] is not None else "-"
        age = f'{c["last_frame_age_s"]:.0f}s' if c["last_frame_age_s"] is not None else "-"
        rows.append(
            f'<tr><td><b>{cam}</b></td>'
            f'<td style="color:{color};font-weight:600">{c["state"]}</td>'
            f'<td>{fpm}</td><td>{age}</td><td>{c["disconnects_24h"]}</td></tr>')
    disk = (f'{status["disk_free_gb"]:.0f} GB'
            if status["disk_free_gb"] is not None else "-")
    rsrp = f'{status["rsrp"]:.0f} dBm' if status["rsrp"] is not None else "-"
    stamp = time.strftime("%d/%m/%Y %H:%M:%S")
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30"><title>Cameras Porto de Santos</title>
<style>body{{font-family:Segoe UI,sans-serif;margin:2rem;background:#f8f9fa}}
table{{border-collapse:collapse}}td,th{{padding:.5rem 1rem;border-bottom:1px solid #dee2e6;text-align:left}}</style>
</head><body><h1>Cameras Porto de Santos</h1>
<table><tr><th>Camera</th><th>Estado</th><th>frames/min</th><th>Ultimo frame</th><th>Quedas 24h</th></tr>
{''.join(rows)}</table>
<p>Disco D: {disk} &middot; RSRP: {rsrp} &middot; atualizado {stamp} (recarrega a cada 30s)</p>
</body></html>"""


def run_panel(store: Store, cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            now = time.time()
            if self.path == "/api/status":
                body = json.dumps(build_status(store, cfg, now)).encode()
                ctype = "application/json"
            else:
                body = render_html(build_status(store, cfg, now)).encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("0.0.0.0", cfg.panel.port), Handler).serve_forever()
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_bot.py tests/test_panel.py tests/test_summary.py -v` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "feat: bot /status e /grafico, painel local e resumo diario"`

---

### Task 14: `service.py` — fiação + instalação no PC

**Files:**
- Create: `monitor/service.py`, `monitor/__main__.py`, `deploy/pc/install_monitor_task.ps1`, `docs/runbooks/fase2-pc.md`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: tudo acima.
- Produces: `service.main(config_path: str = "config.toml")`; threads supervisionadas (`_forever` reinicia worker que morrer, logando `add_event(origin="monitor", kind="worker_error")`). Loops: taplog (2 s → `add_event` por LogEvent), watcher (10 s → `add_sample` de `frames_min` e `last_frame_age_s`), disco (60 s → `disk_free_gb` via `shutil.disk_usage`), cpe (se enabled, `interval_s` → `rsrp`/`rsrq`/`cpe_up`), avaliação de alertas (30 s → monta `Snapshot` e envia `Alert`s: `wants_chart` ⇒ `send_photo(render_camera_chart(...))`, senão `send_text`; origin "*" usa gráfico da primeira câmera caída), bot getUpdates (se telegram enabled), painel (thread), flush outbox (60 s), resumo diário (dispara quando a hora local cruza `summary_hour`), purge (1×/dia).
- A parte testável isolada: `service.build_snapshot(store, cfg, now, disk_free_gb) -> Snapshot` e `service.should_send_summary(last_sent_date: str | None, now: float, hour: int) -> bool`.

- [ ] **Step 1: Teste que falha**

`tests/test_service.py`:
```python
import time
from pathlib import Path
from monitor.config import load_config
from monitor.service import build_snapshot, should_send_summary
from monitor.store import Store

def test_build_snapshot_reads_store(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = time.time()
    s.add_sample(now - 5, "102", "frames_min", 50.0)
    s.add_sample(now - 5, "102", "last_frame_age_s", 12.0)
    for i in range(5):
        s.add_event(now - 60 * i, "102", "DISCONNECT", "x")
    snap = build_snapshot(s, cfg, now, disk_free_gb=100.0)
    c = snap.cameras["102"]
    assert c.frames_per_min == 50.0 and c.last_frame_age_s == 12.0
    assert c.disconnects_15min == 5
    assert snap.cameras["105"].last_frame_age_s is None
    assert snap.disk_free_gb == 100.0
    s.close()

def test_should_send_summary_once_per_day():
    ts_0830 = time.mktime((2026, 8, 19, 8, 30, 0, 0, 0, -1))
    ts_0730 = time.mktime((2026, 8, 19, 7, 30, 0, 0, 0, -1))
    assert should_send_summary(None, ts_0830, 8) is True
    assert should_send_summary("2026-08-19", ts_0830, 8) is False
    assert should_send_summary("2026-08-18", ts_0730, 8) is False   # antes das 8
    assert should_send_summary("2026-08-18", ts_0830, 8) is True
```

- [ ] **Step 2: Rodar e ver falhar**, depois implementar `monitor/service.py`:

```python
"""Fiacao do monitor: threads supervisionadas sobre os modulos puros."""
from __future__ import annotations
import shutil
import threading
import time
from pathlib import Path

from monitor.alerts import AlertEngine, CamState, Snapshot
from monitor.baseline import hourly_baseline
from monitor.bot import BotHandler
from monitor.charts import render_camera_chart
from monitor.config import Config, load_config
from monitor.cpe import CpeScraper
from monitor.panel import run_panel
from monitor.store import Store
from monitor.summary import build_daily_summary
from monitor.taplog import LogFollower
from monitor.telegram import TelegramClient
from monitor.watcher import FrameWatcher


def build_snapshot(store: Store, cfg: Config, now: float,
                   disk_free_gb: float) -> Snapshot:
    cams = {}
    for cam in cfg.cameras:
        fpm = store.last_sample(cam, "frames_min")
        age = store.last_sample(cam, "last_frame_age_s")
        hour = time.localtime(now).tm_hour
        cams[cam] = CamState(
            frames_per_min=fpm[1] if fpm else None,
            last_frame_age_s=age[1] if age else None,
            baseline=hourly_baseline(store, cam, hour, now),
            disconnects_15min=store.count_events(
                cam, "DISCONNECT", now - cfg.alerts.flap_window_s),
        )
    rsrp = store.last_sample("cpe", "rsrp")
    rsrq = store.last_sample("cpe", "rsrq")
    fresh = cfg.cpe.interval_s * 3
    return Snapshot(
        cameras=cams,
        disk_free_gb=disk_free_gb,
        rsrp=rsrp[1] if rsrp and now - rsrp[0] < fresh else None,
        rsrq=rsrq[1] if rsrq and now - rsrq[0] < fresh else None,
    )


def should_send_summary(last_sent_date, now, hour) -> bool:
    lt = time.localtime(now)
    today = time.strftime("%Y-%m-%d", lt)
    return lt.tm_hour >= hour and last_sent_date != today


def _forever(store: Store, name: str, fn, interval: float):
    def run():
        while True:
            t0 = time.time()
            try:
                fn()
            except Exception as e:
                try:
                    store.add_event(time.time(), "monitor", "worker_error",
                                    f"{name}: {e!r}")
                except Exception:
                    pass
            time.sleep(max(0.5, interval - (time.time() - t0)))
    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    return t


def main(config_path: str = "config.toml"):
    cfg = load_config(Path(config_path))
    store = Store(cfg.paths.data / "monitor.db")
    tg = TelegramClient(cfg.telegram, cfg.paths.data) if cfg.telegram.enabled else None
    engine = AlertEngine(cfg.alerts)
    watcher = FrameWatcher(cfg.paths.images, {c: v.save_every
                                              for c, v in cfg.cameras.items()})
    follower = LogFollower(cfg.paths.log, on_event=lambda ev: store.add_event(
        ev.ts, ev.client or "server", ev.kind, ev.detail))
    scraper = CpeScraper(cfg.cpe) if cfg.cpe.enabled else None
    state = {"summary_date": None, "last_purge": 0.0}

    def watch():
        now = time.time()
        for s in watcher.poll(now):
            if s.frames_per_min is not None:
                store.add_sample(now, s.camera, "frames_min", s.frames_per_min)
            if s.last_frame_age_s is not None:
                store.add_sample(now, s.camera, "last_frame_age_s", s.last_frame_age_s)

    def disk():
        free_gb = shutil.disk_usage(cfg.paths.images.anchor or "C:\\").free / 1e9
        store.add_sample(time.time(), "pc", "disk_free_gb", free_gb)

    def cpe():
        now = time.time()
        r = scraper.fetch()
        store.add_sample(now, "cpe", "cpe_up", 0.0 if r is None else 1.0)
        if r:
            if r.rsrp is not None:
                store.add_sample(now, "cpe", "rsrp", r.rsrp)
            if r.rsrq is not None:
                store.add_sample(now, "cpe", "rsrq", r.rsrq)

    def alerts():
        now = time.time()
        last_disk = store.last_sample("pc", "disk_free_gb")
        snap = build_snapshot(store, cfg, now,
                              last_disk[1] if last_disk else 1e9)
        for a in engine.evaluate(now, snap):
            store.add_event(now, a.origin, f"alert_{a.kind}", a.text)
            if tg is None:
                continue
            if a.wants_chart:
                cam = a.origin if a.origin in cfg.cameras else next(iter(cfg.cameras))
                tg.send_photo(render_camera_chart(store, cam, now), a.text)
            else:
                tg.send_text(a.text)
        if should_send_summary(state["summary_date"], now, cfg.summary_hour):
            state["summary_date"] = time.strftime("%Y-%m-%d", time.localtime(now))
            if tg:
                tg.send_text(build_daily_summary(store, cfg, now))
        if now - state["last_purge"] > 86400:
            state["last_purge"] = now
            store.purge(now)

    def bot_loop():
        handler = BotHandler(store, cfg)
        offset = 0
        while True:
            for upd in tg.get_updates(offset):
                offset = upd["update_id"] + 1
                text = upd.get("message", {}).get("text", "")
                if text.startswith("/"):
                    reply, png = handler.handle(text, time.time())
                    if png:
                        tg.send_photo(png, reply)
                    else:
                        tg.send_text(reply)

    _forever(store, "taplog", follower.poll, 2)
    _forever(store, "watcher", watch, 10)
    _forever(store, "disk", disk, 60)
    if scraper:
        _forever(store, "cpe", cpe, cfg.cpe.interval_s)
    _forever(store, "alerts", alerts, 30)
    if tg:
        _forever(store, "outbox", tg.flush_outbox, 60)
        _forever(store, "bot", bot_loop, 1)
    threading.Thread(target=run_panel, args=(store, cfg), daemon=True).start()
    print(f"monitor no ar - painel em http://localhost:{cfg.panel.port}")
    while True:
        time.sleep(60)
```

`monitor/__main__.py`:
```python
import sys
from monitor.service import main

main(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
```

- [ ] **Step 3: Rodar e ver passar** — `python -m pytest tests/test_service.py -v` → PASS; e `python -m pytest -v` (suíte inteira) → PASS.

- [ ] **Step 4: Escrever `deploy/pc/install_monitor_task.ps1`**

```powershell
# Registra o monitor como tarefa agendada: inicia no boot e reinicia se cair.
# Rodar como Administrador na pasta do repo clonado no PC.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = (Get-Command python).Source

$action = New-ScheduledTaskAction -Execute $py -Argument "-m monitor" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 3650) -StartWhenAvailable
Register-ScheduledTask -TaskName "MonitorCamerasPorto" -Action $action `
  -Trigger $trigger -Settings $settings -RunLevel Highest -Force
Start-ScheduledTask -TaskName "MonitorCamerasPorto"
Write-Host "OK. Painel: http://localhost:8080  |  Get-ScheduledTask MonitorCamerasPorto"
```

- [ ] **Step 5: Verificar parse** — `[void][scriptblock]::Create((Get-Content -Raw deploy/pc/install_monitor_task.ps1))` → sem erro.

- [ ] **Step 6: Escrever `docs/runbooks/fase2-pc.md`**

```markdown
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
```

- [ ] **Step 7: Commit** — `git commit -m "feat: service com threads supervisionadas + instalacao no PC"`

---

### Task 15: Teste de integração — replay de um dia

**Files:**
- Test: `tests/test_integration_replay.py`

**Interfaces:**
- Consumes: `LogFollower`, `Store`, `AlertEngine`, `build_snapshot` — o ciclo completo sem rede e sem Telegram.

- [ ] **Step 1: Escrever o teste**

`tests/test_integration_replay.py`:
```python
"""Replay: log de conexoes + amostras sinteticas -> alertas esperados."""
import time
from pathlib import Path
from monitor.alerts import AlertEngine
from monitor.config import load_config
from monitor.service import build_snapshot
from monitor.store import Store
from monitor.taplog import LogFollower

def test_flapping_then_down_then_recovery(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    store = Store(tmp_path / "m.db")
    engine = AlertEngine(cfg.alerts)
    t0 = time.time()

    # 1) flapping: 6 quedas da 106 em 10 min, registradas via taplog
    log = tmp_path / "log.txt"
    lines = []
    for i in range(6):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0 - 600 + i * 100))
        lines.append(f"{stamp} | DISCONNECT | client=106 | [106] Timeout | total=1")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    f = LogFollower(log, on_event=lambda ev: store.add_event(
        ev.ts, ev.client or "server", ev.kind, ev.detail))
    assert f.poll() == 6

    # cameras com frames ok
    for cam in ("102", "105", "106"):
        store.add_sample(t0, cam, "frames_min", 50.0)
        store.add_sample(t0, cam, "last_frame_age_s", 10.0)

    out = engine.evaluate(t0, build_snapshot(store, cfg, t0, disk_free_gb=200.0))
    assert [(a.kind, a.origin) for a in out] == [("flapping", "106")]

    # 2) down: a 102 para de entregar
    t1 = t0 + 60
    store.add_sample(t1, "102", "last_frame_age_s", 300.0)
    out = engine.evaluate(t1, build_snapshot(store, cfg, t1, disk_free_gb=200.0))
    assert ("down", "102") in [(a.kind, a.origin) for a in out]

    # 3) recovery
    t2 = t1 + 300
    store.add_sample(t2, "102", "last_frame_age_s", 5.0)
    out = engine.evaluate(t2, build_snapshot(store, cfg, t2, disk_free_gb=200.0))
    assert [(a.kind, a.origin) for a in out] == [("recovered", "102")]
    store.close()
```

- [ ] **Step 2: Rodar** — `python -m pytest tests/test_integration_replay.py -v` → PASS (se falhar, o bug está na integração entre módulos — corrigir antes de seguir).

- [ ] **Step 3: Rodar a suíte inteira** — `python -m pytest -v` → tudo PASS.

- [ ] **Step 4: Commit** — `git commit -m "test: replay de integracao (flapping -> down -> recovery)"`

---

## Self-review (feita na escrita do plano)

- **Cobertura da spec:** Fase 1 → Tasks 2–5 (1.1=T3, 1.2=T4, 1.3=T2, 1.4=T5+T4, 1.5=runbook D); Fase 2 → Tasks 6–15 (watcher=T8, taplog=T7, cpe=T12, disco/idade=T14, linha de base=T9, 7 regras de alerta=T10, bot=T13, painel=T13, resumo=T13, retenção=T6, anti-ruído=T10). Fase 3: plano futuro, por decisão registrada no cabeçalho.
- **Pendências da spec respeitadas:** variante da 105 (runbook C.10), regex do CPE calibrado em campo com `--probe` e `enabled=false` por default, `LOG_connections.txt` histórico entra na linha de base naturalmente quando o monitor rodar.
- **Consistência de tipos:** assinaturas de `Store`, `CamState/Snapshot/Alert`, `CameraSample`, `LogEvent`, `Config` conferidas entre as tasks que produzem e consomem.
