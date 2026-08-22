"""Guardas do instalador da tarefa agendada do monitor.

Nao da para registrar a tarefa aqui (outra maquina, precisa de Administrador),
entao o teste trava as tres decisoes que, erradas, fazem a tarefa existir sem
funcionar.
"""
from pathlib import Path

SCRIPT = Path("deploy/pc/install_monitor_task.ps1")


def _src():
    return SCRIPT.read_text(encoding="utf-8")


def test_roda_como_system_e_nao_como_usuario_interativo():
    """Gatilho AtStartup registrado sob usuario interativo so dispara depois do
    logon - o monitor ficaria fora ate alguem entrar na maquina, que e'
    exatamente a pane que a tarefa deveria evitar."""
    src = _src()
    assert "New-ScheduledTaskPrincipal" in src
    assert "ServiceAccount" in src
    assert "-Principal" in src


def test_nao_confia_no_python_do_path():
    """O monitor roda no python do Anaconda; (Get-Command python).Source pode
    apontar para outro interpretador, sem matplotlib."""
    src = _src()
    assert "param(" in src
    assert "$Python" in src


def test_verifica_o_config_antes_de_registrar():
    """config.toml esta no .gitignore: um clone novo nasce sem ele, e a tarefa
    registraria feliz um monitor que morre em todo start."""
    assert "config.toml" in _src()


def test_verifica_que_o_python_escolhido_importa_o_monitor():
    src = _src()
    assert "-m monitor --help" in src or "import monitor" in src


def test_avisa_se_ja_houver_monitor_rodando():
    """Dois monitores no ar disputam o getUpdates do Telegram e o mesmo SQLite."""
    src = _src()
    assert "-m monitor*" in src or "CommandLine" in src


def test_impede_duas_instancias_da_tarefa():
    assert "MultipleInstances" in _src()


def test_powershell_sem_erro_de_sintaxe():
    """Erro de sintaxe so apareceria no PC do SPA, com o monitor ja parado."""
    import subprocess
    alvo = str(SCRIPT.resolve()).replace("'", "''")
    cmd = ("$e = $null; [void][System.Management.Automation.Language.Parser]::"
           f"ParseFile('{alvo}', [ref]$null, [ref]$e); "
           "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                        "-Command", cmd],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_nao_usa_escape_de_aspas_do_c():
    r"""Em PowerShell \" nao escapa aspas - vira barra-espaco e as aspas somem.
    O parser aceita, entao so o texto impresso sai errado: instrucao quebrada
    justo na hora em que a pessoa esta com o monitor parado."""
    assert '\\"' not in _src()
