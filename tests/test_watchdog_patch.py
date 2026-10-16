import importlib.util
import sys
import types
from pathlib import Path

WD_PATH = Path("deploy/rasp/watchdog_test.py")


class _FakeOptions:
    def __init__(self):
        self.binary_location = None
        self.args = []

    def add_argument(self, a):
        self.args.append(a)


def _fake_selenium(chrome_factory, vistos):
    """Registra modulos selenium falsos; chrome_factory() vira webdriver.Chrome()."""
    selenium = types.ModuleType("selenium")
    webdriver = types.ModuleType("selenium.webdriver")

    def chrome(*a, **k):
        vistos.append(k.get("options"))
        return chrome_factory()

    webdriver.Chrome = chrome
    common_by = types.ModuleType("selenium.webdriver.common.by")
    common_by.By = types.SimpleNamespace(XPATH="xpath")
    chrome_opts = types.ModuleType("selenium.webdriver.chrome.options")
    chrome_opts.Options = _FakeOptions
    chrome_svc = types.ModuleType("selenium.webdriver.chrome.service")
    chrome_svc.Service = lambda *a: None
    excecoes = types.ModuleType("selenium.common.exceptions")
    excecoes.WebDriverException = WebDriverException
    excecoes.NoSuchElementException = NoSuchElementException
    selenium.webdriver = webdriver
    for name, mod in {
        "selenium": selenium,
        "selenium.common": types.ModuleType("selenium.common"),
        "selenium.common.exceptions": excecoes,
        "selenium.webdriver": webdriver,
        "selenium.webdriver.common": types.ModuleType("selenium.webdriver.common"),
        "selenium.webdriver.common.by": common_by,
        "selenium.webdriver.chrome": types.ModuleType("selenium.webdriver.chrome"),
        "selenium.webdriver.chrome.options": chrome_opts,
        "selenium.webdriver.chrome.service": chrome_svc,
    }.items():
        sys.modules[name] = mod


def _load(chrome_factory, tmp_path, monkeypatch, vistos=None):
    _fake_selenium(chrome_factory, vistos if vistos is not None else [])
    spec = importlib.util.spec_from_file_location("watchdog_test", WD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # tudo o que ele escreve vai para o tmp_path, nunca para /home/pi
    monkeypatch.setattr(mod, "LOG", str(tmp_path / "router.log"))
    monkeypatch.setattr(mod, "TRAVA", str(tmp_path / "router.lock"))
    monkeypatch.setattr(mod, "ESTADO", str(tmp_path / "router.ultimo"))
    monkeypatch.setattr(mod, "CONF", str(tmp_path / "router.conf"))
    monkeypatch.setattr(mod, "ERRO_HTML", str(tmp_path / "router_erro.html"))
    monkeypatch.setattr(mod, "ERRO_PNG", str(tmp_path / "router_erro.png"))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


class WebDriverException(Exception):
    pass


class NoSuchElementException(WebDriverException):
    pass


# Campos de login que so existem no firmware novo da Elsys (16/10/2026).
NOVO_USUARIO = "/html/body/div/div[2]/form/div[1]/input"
NOVO_SENHA = "/html/body/div/div[2]/form/div[2]/input"


class FakeElement:
    def __init__(self, d, xp, rotulo="Reiniciar", visivel=True):
        self.d, self.xp, self.rotulo, self.visivel = d, xp, rotulo, visivel
        self.text = ""

    def click(self):
        self.d.calls.append(("click", self.xp))

    def send_keys(self, v):
        self.d.calls.append(("keys", v))

    def is_displayed(self):
        return self.visivel

    def get_attribute(self, nome):
        return self.rotulo if nome == "value" else None


class FakeDriver:
    """Pagina falsa. Por padrao, a Elsys de firmware antigo: tudo existe, menos
    os campos de login do firmware novo. `ausentes` some com XPaths; `rotulos`
    da a um XPath os botoes (value) que ele devolve."""

    def __init__(self, ausentes=(NOVO_USUARIO, NOVO_SENHA), rotulos=None, js_erro=None):
        self.calls = []
        self.ausentes = set(ausentes)
        self.rotulos = rotulos or {}
        self.js_erro = js_erro

    def get(self, url):
        self.calls.append(("get", url))

    def _els(self, xp):
        if xp in self.ausentes:
            return []
        return [FakeElement(self, xp, r) for r in self.rotulos.get(xp, ["Reiniciar"])]

    def find_elements(self, by, xp):
        return self._els(xp)

    def find_element(self, by, xp):
        self.calls.append(("find", xp))
        els = self._els(xp)
        if not els:
            raise NoSuchElementException(xp)
        return els[0]

    def execute_script(self, script, el):
        self.calls.append(("js", el.xp, el.rotulo))
        if self.js_erro:
            raise self.js_erro

    def quit(self):
        self.calls.append(("quit", None))


def _driver_ok():
    d = FakeDriver()
    _driver_ok.ultimo = d
    return d


def _fabrica(**kw):
    """Fabrica de driver com pagina sob medida; o ultimo fica em .ultimo."""
    def f():
        d = FakeDriver(**kw)
        f.ultimo = d
        return d
    return f


def test_chrome_falhando_ao_abrir_nao_levanta(tmp_path, monkeypatch):
    """O F7: aqui o original estourava NameError e matava o cliente inteiro."""
    def boom():
        raise RuntimeError("DevToolsActivePort file doesn't exist")
    mod = _load(boom, tmp_path, monkeypatch)
    assert mod.restart_router() == -1
    assert "DevToolsActivePort" in (tmp_path / "router.log").read_text()


def test_caminho_feliz_fecha_o_driver(tmp_path, monkeypatch):
    """Firmware antigo, sem router.conf: o fluxo de sempre, sem senha."""
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    assert mod.restart_router() == 0
    calls = _driver_ok.ultimo.calls
    assert ("quit", None) in calls
    assert ("click", mod.ELSYS_BTN_LOGIN_ANTIGO) in calls
    assert ("js", mod.ELSYS_BTN_REBOOT_ANTIGO, "Reiniciar") in calls
    assert "firmware antigo" in (tmp_path / "router.log").read_text()


def test_firmware_novo_faz_login_com_a_senha_do_router_conf(tmp_path, monkeypatch):
    """A 102 em 16/10: tela de login e botao oculto, clicado por JS."""
    f = _fabrica(ausentes=())
    mod = _load(f, tmp_path, monkeypatch)
    Path(mod.CONF).write_text("senha = segredo\n")
    assert mod.restart_router() == 0
    calls = f.ultimo.calls
    assert ("keys", "admin") in calls and ("keys", "segredo") in calls
    assert ("click", mod.ELSYS_BTN_LOGIN) in calls
    assert ("js", mod.ELSYS_BTN_REBOOT, "Reiniciar") in calls
    assert [c[1] for c in calls if c[0] == "get"] == [mod.PADRAO["url"]] * 2


def test_firmware_novo_sem_senha_nao_tenta_e_diz_o_que_falta(tmp_path, monkeypatch):
    """A senha nao mora no codigo (repositorio publico): sem router.conf, falha
    dizendo onde ela tem de estar."""
    f = _fabrica(ausentes=())
    mod = _load(f, tmp_path, monkeypatch)
    assert mod.restart_router() == -1
    assert not [c for c in f.ultimo.calls if c[0] in ("keys", "js")]
    assert "router.conf nao tem 'senha'" in (tmp_path / "router.log").read_text()


def test_pagina_que_cai_no_clique_conta_como_reinicio(tmp_path, monkeypatch):
    f = _fabrica(js_erro=WebDriverException("stale element reference"))
    mod = _load(f, tmp_path, monkeypatch)
    assert mod.restart_router() == 0
    assert "pagina caiu junto (WebDriverException)" in (tmp_path / "router.log").read_text()


def test_nunca_clica_em_reset_de_fabrica(tmp_path, monkeypatch):
    """Um reset apaga o APN e o encaminhamento: so volta com alguem no poste.
    Pula o botao com cara de reset e fica com o reinicio de verdade."""
    rot = {}
    f = _fabrica(rotulos=rot)
    mod = _load(f, tmp_path, monkeypatch)
    rot.update({mod.ELSYS_BTN_REBOOT_ANTIGO: ["Restaurar Padrão de Fábrica"],
                mod.ELSYS_BTN_REBOOT: [],
                mod.ELSYS_BTN_REBOOT_TEXTO: ["Reset to factory defaults", "Reiniciar"]})
    assert mod.restart_router() == 0
    js = [c for c in f.ultimo.calls if c[0] == "js"]
    assert js == [("js", mod.ELSYS_BTN_REBOOT_TEXTO, "Reiniciar")]
    assert "parece reset de fabrica" in (tmp_path / "router.log").read_text()


def test_so_reset_na_pagina_nao_clica_nada(tmp_path, monkeypatch):
    rot = {}
    f = _fabrica(rotulos=rot)
    mod = _load(f, tmp_path, monkeypatch)
    for xp in (mod.ELSYS_BTN_REBOOT_ANTIGO, mod.ELSYS_BTN_REBOOT, mod.ELSYS_BTN_REBOOT_TEXTO):
        rot[xp] = ["Factory reset"]
    assert mod.restart_router() == -1
    assert not [c for c in f.ultimo.calls if c[0] == "js"]


def test_seco_acha_o_botao_sem_clicar_e_sem_marcar(tmp_path, monkeypatch):
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    assert mod.restart_router(seco=True) == 0
    assert not [c for c in _driver_ok.ultimo.calls if c[0] == "js"]
    assert "--seco ok" in (tmp_path / "router.log").read_text()
    assert mod.restart_router() == 0           # nada marcado: reinicia de verdade


def test_reinicio_de_teste_protege_contra_o_segundo(tmp_path, monkeypatch):
    """Teste reinicia de verdade: o cliente rodando nao pode derrubar de novo."""
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    assert mod.restart_router(teste=True) == 0
    assert mod.restart_router() == -2


def test_o_log_carrega_a_hora(tmp_path, monkeypatch):
    """Sem hora, o router.log nao se cruza com o LOG_connections.txt - foi o
    que impediu de datar as falhas da 105 na leitura de 27/08."""
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    mod.restart_router()
    primeira = (tmp_path / "router.log").read_text().splitlines()[0]
    assert primeira[:2].isdigit() and " | " in primeira


def test_sobe_o_chrome_com_as_flags_que_o_fazem_subir_sem_tela(tmp_path, monkeypatch):
    vistos = []
    mod = _load(_driver_ok, tmp_path, monkeypatch, vistos)
    assert mod.restart_router() == 0
    args = vistos[0].args
    for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
        assert flag in args
    assert vistos[0].binary_location == mod.CHROMIUM


def test_cada_chamada_usa_um_perfil_proprio_e_o_apaga(tmp_path, monkeypatch):
    """Duas chamadas sobre o perfil default e' a explicacao do DevToolsActivePort
    intermitente medido na 105 em 27/08."""
    import os
    vistos = []
    mod = _load(_driver_ok, tmp_path, monkeypatch, vistos)
    monkeypatch.setattr(mod, "MIN_INTERVALO_S", 0)
    assert mod.restart_router() == 0
    assert mod.restart_router() == 0
    perfis = [a.split("=", 1)[1] for op in vistos for a in op.args
              if a.startswith("--user-data-dir=")]
    assert len(perfis) == 2 and perfis[0] != perfis[1]
    assert not any(os.path.exists(p) for p in perfis)   # limpos no fim


def test_nao_reinicia_o_cpe_de_novo_antes_de_ele_subir(tmp_path, monkeypatch):
    """O cliente chama isto a cada connect() que falha - de 90 em 90s no inicio
    do backoff. Sem piso, o CPE e' derrubado antes de terminar de se registrar."""
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    assert mod.restart_router() == 0
    assert mod.restart_router() == -2
    assert "deixando ele subir" in (tmp_path / "router.log").read_text()


def test_falha_nao_conta_como_reinicio_e_pode_tentar_de_novo(tmp_path, monkeypatch):
    """Tentativa que nem abriu o Chrome nao reiniciou o CPE: nao ha o que esperar."""
    def boom():
        raise RuntimeError("Chrome failed to start")
    mod = _load(boom, tmp_path, monkeypatch)
    assert mod.restart_router() == -1
    assert mod.restart_router() == -1


def test_duas_chamadas_simultaneas_nao_abrem_dois_chromes(tmp_path, monkeypatch):
    vistos = []
    mod = _load(_driver_ok, tmp_path, monkeypatch, vistos)
    Path(mod.TRAVA).write_text("999999")     # outra chamada em andamento
    assert mod.restart_router() == -2
    assert vistos == []


def test_trava_de_processo_morto_nao_bloqueia_para_sempre(tmp_path, monkeypatch):
    import os
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    Path(mod.TRAVA).write_text("999999")
    velho = __import__("time").time() - mod.TRAVA_VALIDADE_S - 10
    os.utime(mod.TRAVA, (velho, velho))
    assert mod.restart_router() == 0


def test_router_conf_escolhe_o_teltonika_sem_senha_no_repositorio(tmp_path, monkeypatch):
    """As duas variantes convivem: a Elsys e' o padrao, a Teltonika vem do
    router.conf, que fica fora do git justamente por causa da senha."""
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    Path(mod.CONF).write_text(
        "modelo = teltonika\n"
        "url = http://192.168.11.1/\n"
        "usuario = admin\n"
        "senha = segredo\n")
    assert mod.restart_router() == 0
    calls = _driver_ok.ultimo.calls
    assert ("keys", "admin") in calls and ("keys", "segredo") in calls
    assert ("get", "http://192.168.11.1/") in calls


def test_sem_router_conf_o_padrao_e_a_elsys(tmp_path, monkeypatch):
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    assert mod.restart_router() == 0
    urls = [c[1] for c in _driver_ok.ultimo.calls if c[0] == "get"]
    assert urls == ["http://192.168.11.254/index.html?status"]


def test_log_impossivel_nao_derruba_o_reinicio(tmp_path, monkeypatch):
    mod = _load(_driver_ok, tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "LOG", str(tmp_path / "nao" / "existe" / "r.log"))
    assert mod.restart_router() == 0
