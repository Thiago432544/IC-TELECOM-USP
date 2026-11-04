"""Reinicia o CPE pelo web UI sem nunca derrubar quem chama.

O original fazia `driver.quit()` no `except` com `driver` inexistente ->
NameError. Como restart_router() e' chamada de dentro do `except` do main() do
cliente, esse erro subia sem ninguem para pega-lo e **matava o cliente**; sem
autostart, a camera ficava morta ate alguem entrar por VNC. E' o F7.

Em 27/08/2026 o gatilho foi medido, no router.log da 105:

    Erro ao reiniciar o roteador: session not created: Chrome failed to start:
    exited normally. (DevToolsActivePort file doesn't exist)

intermitente - sucesso, falha, falha, sucesso - com 3,2 GB de RAM livre e
nenhum chromium zumbi. Nao era falta de recurso. A explicacao que sobra e' que
**o cliente e o supervisor de heartbeat chamam esta mesma funcao**: duas
chamadas proximas abrem dois Chromes sobre o mesmo perfil e o segundo morre
assim. A 105 so sobreviveu porque a copia dela ja estava blindada.

Dai as quatro defesas, alem da blindagem:

- perfil proprio e descartavel por chamada, nunca o default do usuario;
- trava de arquivo, para duas chamadas nao abrirem dois Chromes;
- intervalo minimo entre reinicios de verdade - reiniciar o CPE a cada
  tentativa de conexao nao deixa ele terminar de se registrar na rede, que e'
  o mecanismo suspeito de segurar a recuperacao das Elsys;
- flags de headless/sandbox/shm, que e' o que faz o Chrome subir quando nao ha
  sessao grafica.

E cada linha do log passa a ter hora. Sem isso o router.log nao se cruza com o
LOG_connections.txt, que foi exatamente o que impediu de datar as falhas da 105
na leitura de 27/08.

Dois firmwares de Elsys convivem desde 16/10/2026, quando a 102 foi atualizada:

- antigo: sem senha; um botao abre a secao do sistema e o reboot esta na
  tabela dela;
- novo: tela de login com usuario e senha, e o botao de reboot oculto mais
  fundo na pagina - clicado por JavaScript (adaptacao feita pelo time em
  campo, validada na 102 em 16/10).

O fluxo descobre qual e' pela pagina. O firmware novo exige a senha no
router.conf: **nunca no codigo**, o repositorio e' publico.

E nenhum botao com cara de reset de fabrica e' clicado, venha do XPath ou da
busca por texto: um reset apaga o APN da rede privada e o encaminhamento de
portas, e a camera so volta com alguem no poste.

Teltonika (RUT200, nov/2026): reinicio pela **API REST**, sem Chrome nem
Selenium - o Chrome que nao abre foi a causa do F7. Login em /api/login (o
mesmo que o radio_log.py usa desde 28/08 neste firmware), leitura do modem em
/api/modems/status, que vai para o log, e o reboot num endpoint de acao. O
endpoint certo do firmware em campo se confirma na bancada com --seco, que o
testa por GET sem reiniciar. O fluxo antigo pela pagina continua disponivel
com `via = pagina`.

Instalar em: /home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/watchdog_test.py
Configuracao (fora do git, ao lado do script): router.conf
    modelo = elsys | teltonika
    url    = http://192.168.11.254/index.html?status   (Teltonika: http://192.168.11.1)
    usuario = admin
    senha   = ...          # obrigatoria no firmware novo da Elsys e na Teltonika
    via     = api | pagina # so Teltonika; padrao api
    api_reboot = /api/system/actions/reboot   # so se a bancada mostrar outro

Na mao:
    python3 watchdog_test.py --seco   # login + confere o reboot, sem reiniciar
    python3 watchdog_test.py --test   # reinicia de verdade
"""
import json
import os
import shutil
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

BASE = "/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL"
LOG = BASE + "/router.log"
TRAVA = BASE + "/router.lock"
ESTADO = BASE + "/router.ultimo"
CONF = BASE + "/router.conf"

CHROMIUM = "/usr/bin/chromium"
CHROMEDRIVER = "/usr/bin/chromedriver"

# Um reinicio de CPE tira o enlace do ar por ~1 min e o registro na rede leva
# mais. O cliente tenta reconectar a cada 90s no inicio do backoff: sem este
# piso, ele derruba o CPE de novo antes de ele terminar de subir.
MIN_INTERVALO_S = 600
# Trava mais velha que isto e' de um processo que morreu sem limpar.
TRAVA_VALIDADE_S = 300

FLAGS = (
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,1024",
)

# Firmware antigo: sem senha.
ELSYS_BTN_LOGIN_ANTIGO = "/html/body/div[1]/div[2]/form/div[1]/div[3]/input"
ELSYS_BTN_REBOOT_ANTIGO = ("/html/body/div[1]/div[2]/form/div[5]/div[2]/table"
                           "/tbody/tr[7]/td[2]/input")
# Firmware novo (16/10/2026): login com usuario e senha (senha so no router.conf).
ELSYS_USUARIO = "/html/body/div/div[2]/form/div[1]/input"
ELSYS_SENHA = "/html/body/div/div[2]/form/div[2]/input"
ELSYS_BTN_LOGIN = "/html/body/div/div[2]/form/button"
ELSYS_USUARIO_PADRAO = "admin"
ELSYS_BTN_REBOOT = ("/html/body/div[1]/div[1]/div[1]/div/div[4]/div/form"
                    "/div[5]/div[2]/table/tbody/tr[7]/td[2]/input")
# Fallback se nenhum XPath acima existir mais na pagina.
ELSYS_BTN_REBOOT_TEXTO = (
    "//input[contains(translate(@value,'REBOTIINA','rebotiina'),'reboot') or "
    "contains(translate(@value,'REINICAR','reinicar'),'reinici')]"
    " | //button[contains(translate(.,'REBOTIINCA','rebotiinca'),'reboot') or "
    "contains(translate(.,'REINICAR','reinicar'),'reinici')]")
# Rotulos que nunca sao clicados (minusculo, sem acento): reset de fabrica.
PROIBIDOS = ("fabric", "factory", "padr", "default", "restaur", "reset")
ERRO_HTML = BASE + "/router_erro.html"
ERRO_PNG = BASE + "/router_erro.png"
TELT_USUARIO ='//*[@id="username"]'
TELT_SENHA = '//*[@id="password"]'
TELT_BTN_ENTRAR = "/html/body/div/div/div[1]/form/button"
TELT_BTN_REBOOT = ("/html/body/div/div/div[2]/div[2]/div/div/div/div/div"
                   "/button")
# API REST do RutOS (firmware 07.06+; a bancada roda RUT2M_R_00.07.10.2).
TELT_URL_PADRAO = "http://192.168.11.1"
TELT_API_REBOOT = "/api/system/actions/reboot"
# Testados por GET no --seco, nesta ordem, para achar o do firmware em campo.
TELT_API_REBOOT_CANDIDATOS = (TELT_API_REBOOT, "/api/system/reboot",
                              "/api/system/device/actions/reboot")
TELT_API_NAO_EXISTE = 122          # "Endpoint not implemented"

ECOAR = False        # ligado por --test/--seco: log tambem no terminal

PADRAO = {"modelo": "elsys",
          "url": "http://192.168.11.254/index.html?status",
          "usuario": "",
          "senha": ""}


def _log(msg):
    """Nunca pode derrubar a funcao: o log e' conveniencia, o reinicio nao."""
    linha = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    if ECOAR:
        print(linha)
    try:
        with open(LOG, "a") as f:
            print(linha, file=f)
    except OSError:
        pass


def _config():
    """router.conf; ausente, vale o padrao (Elsys, sem senha: so o firmware antigo)."""
    cfg = dict(PADRAO)
    try:
        with open(CONF) as f:
            for linha in f:
                linha = linha.split("#", 1)[0].strip()
                if "=" in linha:
                    k, v = linha.split("=", 1)
                    cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


def _cedo_demais():
    """True se o CPE foi reiniciado ha pouco e ainda merece tempo de subir."""
    try:
        with open(ESTADO) as f:
            ultimo = float(f.read().strip())
    except (OSError, ValueError):
        return False
    return (time.time() - ultimo) < MIN_INTERVALO_S


def _marca_reinicio():
    try:
        with open(ESTADO, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _pega_trava():
    """Trava exclusiva por arquivo. Devolve True se conseguiu."""
    try:
        fd = os.open(TRAVA, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        pass
    except OSError:
        return True          # sem poder travar, seguir e' melhor que travar
    try:
        if time.time() - os.path.getmtime(TRAVA) > TRAVA_VALIDADE_S:
            os.unlink(TRAVA)
            return _pega_trava()
    except OSError:
        pass
    return False


def _solta_trava():
    try:
        os.unlink(TRAVA)
    except OSError:
        pass


def _abre_chrome(perfil, teste=False):
    options = Options()
    options.binary_location = CHROMIUM
    for flag in FLAGS:
        # Em teste a janela fica visivel, para acompanhar o fluxo.
        if teste and flag.startswith("--headless"):
            continue
        options.add_argument(flag)
    # Perfil proprio por chamada: e' o que impede duas chamadas simultaneas de
    # brigarem pelo diretorio default e produzirem DevToolsActivePort.
    options.add_argument(f"--user-data-dir={perfil}")
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)


def _dump_pagina(driver):
    """Guarda HTML e screenshot do ponto da falha, para achar o XPath certo."""
    try:
        with open(ERRO_HTML, "w") as f:
            f.write(f"<!-- {driver.current_url} -->\n{driver.page_source}")
        driver.save_screenshot(ERRO_PNG)
        _log(f"pagina da falha salva em {ERRO_HTML} e {ERRO_PNG}")
    except Exception:
        pass


def _sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower())
                   if not unicodedata.combining(c))


def _rotulo(el):
    """O que o botao diz: value de <input>, texto de <button>."""
    for obter in (lambda: el.get_attribute("value"), lambda: el.text):
        try:
            v = obter()
        except Exception:
            v = None
        if v and v.strip():
            return v.strip()
    return ""


def _proibido(rotulo):
    t = _sem_acento(rotulo)
    return any(p in t for p in PROIBIDOS)


def _acha_reboot(driver, caminhos):
    """Primeiro botao, na ordem dos caminhos, que nao tenha cara de reset."""
    for como, xp in caminhos:
        for el in driver.find_elements(By.XPATH, xp):
            rotulo = _rotulo(el)
            if _proibido(rotulo):
                _log(f"elsys: ignorado ({como}): {rotulo!r} parece reset de fabrica")
                continue
            return el, como, rotulo
    raise RuntimeError("botao de reboot nao encontrado na pagina")


def _fluxo_elsys(driver, cfg, clicar=True):
    driver.get(cfg["url"])
    time.sleep(10)
    if (driver.find_elements(By.XPATH, ELSYS_USUARIO)
            and driver.find_elements(By.XPATH, ELSYS_SENHA)):
        if not cfg["senha"]:
            raise RuntimeError("a Elsys pede login (firmware novo) e o "
                               "router.conf nao tem 'senha'")
        driver.find_element(By.XPATH, ELSYS_USUARIO).send_keys(
            cfg["usuario"] or ELSYS_USUARIO_PADRAO)
        driver.find_element(By.XPATH, ELSYS_SENHA).send_keys(cfg["senha"])
        driver.find_element(By.XPATH, ELSYS_BTN_LOGIN).click()
        _log("elsys: firmware novo, login enviado")
        time.sleep(10)
        # O login pode redirecionar; o botao de reboot esta na pagina de status.
        driver.get(cfg["url"])
        time.sleep(10)
        caminhos = (("xpath", ELSYS_BTN_REBOOT),
                    ("xpath antigo", ELSYS_BTN_REBOOT_ANTIGO),
                    ("texto", ELSYS_BTN_REBOOT_TEXTO))
    else:
        driver.find_element(By.XPATH, ELSYS_BTN_LOGIN_ANTIGO).click()
        _log("elsys: firmware antigo, sem senha")
        time.sleep(10)
        caminhos = (("xpath antigo", ELSYS_BTN_REBOOT_ANTIGO),
                    ("xpath", ELSYS_BTN_REBOOT),
                    ("texto", ELSYS_BTN_REBOOT_TEXTO))
    botao, como, rotulo = _acha_reboot(driver, caminhos)
    try:
        visivel = botao.is_displayed()
    except Exception:
        visivel = "?"
    _log(f"elsys: botao de reboot achado ({como}, {rotulo!r}, visivel={visivel})")
    if not clicar:
        _log("elsys: --seco, nao clicado")
        return
    # JS clica mesmo se o botao estiver oculto/coberto ("not interactable").
    try:
        driver.execute_script("arguments[0].click();", botao)
        _log("elsys: reboot clicado")
    except WebDriverException as e:
        # O CPE reinicia e a pagina cai/recarrega no meio do clique, o que o
        # Selenium reporta como erro (stale element etc.). O reboot ja saiu.
        _log(f"elsys: reboot clicado, pagina caiu junto ({type(e).__name__})")


def _fluxo_teltonika(driver, cfg, clicar=True):
    driver.get(cfg["url"])
    time.sleep(10)
    driver.find_element(By.XPATH, TELT_USUARIO).send_keys(cfg["usuario"])
    driver.find_element(By.XPATH, TELT_SENHA).send_keys(cfg["senha"])
    driver.find_element(By.XPATH, TELT_BTN_ENTRAR).click()
    time.sleep(10)
    driver.get(cfg.get("url_reboot", "http://192.168.11.1/status/network/mobile"))
    time.sleep(10)
    botao = driver.find_element(By.XPATH, TELT_BTN_REBOOT)
    if not clicar:
        _log(f"teltonika: botao de reboot achado ({_rotulo(botao)!r}), --seco, nao clicado")
        return
    botao.click()


FLUXOS = {"elsys": _fluxo_elsys, "teltonika": _fluxo_teltonika}


# ------------------------------------------------------- Teltonika por API ---

def _raiz(url):
    p = urllib.parse.urlsplit(url or TELT_URL_PADRAO)
    return f"{p.scheme or 'http'}://{p.netloc or p.path.strip('/')}"


def _api(metodo, url, corpo=None, token=None, timeout=10):
    """(status HTTP, JSON). Erro HTTP volta como resposta, nao como excecao:
    o corpo do erro e' o que diz se o endpoint existe."""
    cab = {}
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        cab["Content-Type"] = "application/json"
    if token:
        cab["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=dados, method=metodo, headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            texto = r.read().decode("utf-8", "replace")
            st = r.status
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "replace")
        st = e.code
    try:
        return st, (json.loads(texto) if texto.strip() else {})
    except ValueError:
        return st, {"bruto": texto[:200]}


def _codigos(resp):
    return {e.get("code") for e in (resp.get("errors") or []) if isinstance(e, dict)}


def _endpoint_existe(st, resp):
    return st != 404 and TELT_API_NAO_EXISTE not in _codigos(resp)


def _api_login(cfg):
    if not cfg.get("senha"):
        raise RuntimeError("a Teltonika pede login e o router.conf nao tem 'senha'")
    raiz = _raiz(cfg.get("url"))
    st, r = _api("POST", raiz + "/api/login",
                 {"username": cfg.get("usuario") or "admin", "password": cfg["senha"]})
    token = (r.get("data") or {}).get("token") if isinstance(r.get("data"), dict) else None
    if not token:
        raise RuntimeError(f"login recusado pela Teltonika (HTTP {st}): "
                           f"{r.get('errors') or r}")
    return raiz, token


def _api_modem(raiz, token):
    """Uma linha de log com o estado do modem; nunca derruba o reinicio."""
    try:
        st, r = _api("GET", raiz + "/api/modems/status", token=token)
        d = r.get("data")
        d = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else {})
        _log(f"teltonika: modem estado={d.get('state')!r} rede={d.get('conntype')!r} "
             f"operadora={d.get('operator')!r} rsrp={d.get('rsrp')} sinr={d.get('sinr')}")
    except Exception as e:                                  # noqa: BLE001
        _log(f"teltonika: estado do modem ilegivel ({type(e).__name__}: {e})")


def _fluxo_teltonika_api(cfg, clicar=True):
    raiz, token = _api_login(cfg)
    _log("teltonika: login pela API ok")
    _api_modem(raiz, token)
    escolhido = cfg.get("api_reboot")
    if not clicar:
        candidatos = (escolhido,) if escolhido else TELT_API_REBOOT_CANDIDATOS
        for caminho in candidatos:
            st, r = _api("GET", raiz + caminho, token=token)
            if _endpoint_existe(st, r):
                _log(f"teltonika: endpoint de reboot {caminho} existe (GET -> HTTP {st}), "
                     "--seco, nao reiniciado")
                if caminho != TELT_API_REBOOT and not escolhido:
                    _log(f"teltonika: ponha 'api_reboot = {caminho}' no router.conf")
                return
            _log(f"teltonika: {caminho} nao existe neste firmware (HTTP {st})")
        raise RuntimeError("nenhum endpoint de reboot conhecido existe neste firmware")
    caminho = escolhido or TELT_API_REBOOT
    try:
        st, r = _api("POST", raiz + caminho, {}, token=token)
    except (urllib.error.URLError, OSError) as e:
        # A Teltonika pode cair antes de responder: o reboot ja saiu.
        _log(f"teltonika: reboot pedido, conexao caiu junto ({type(e).__name__})")
        return
    if st >= 400 or r.get("success") is False:
        raise RuntimeError(f"reboot recusado pela API (HTTP {st}): {r.get('errors') or r}")
    _log(f"teltonika: reboot pedido pela API ({caminho}, HTTP {st})")


def restart_router(teste=False, seco=False):
    """Reinicia o CPE. 0 = reiniciou, -1 = tentou e falhou, -2 = nao tentou.

    Nunca levanta excecao: quem chama esta dentro de um `except` e morreria.

    teste=True (--test): janela visivel e sem o intervalo minimo. O reinicio
    feito em teste e' registrado, para o cliente em execucao nao derrubar o
    CPE de novo enquanto ele sobe.
    seco=True (--seco): faz login e acha o botao, sem clicar - valida o fluxo
    contra a pagina (firmware novo?) sem tirar a camera do ar.
    """
    if not (teste or seco) and _cedo_demais():
        _log("pulando: CPE reiniciado ha menos de "
             f"{MIN_INTERVALO_S}s, deixando ele subir")
        return -2
    if not _pega_trava():
        _log("pulando: ja existe um reinicio em andamento")
        return -2

    _log("reinicializando roteador")
    cfg = _config()
    if cfg["modelo"] == "teltonika" and cfg.get("via", "api") != "pagina":
        try:
            _fluxo_teltonika_api(cfg, not seco)
            if seco:
                _log("--seco ok: login pela API e endpoint de reboot encontrados")
                return 0
            _marca_reinicio()
            _log("roteador reiniciado com sucesso")
            return 0
        except Exception as e:                              # noqa: BLE001
            _log(f"Erro ao reiniciar o roteador: {e}")
            return -1
        finally:
            _solta_trava()
    fluxo = FLUXOS.get(cfg["modelo"], _fluxo_elsys)
    driver = None
    perfil = None
    try:
        perfil = tempfile.mkdtemp(prefix="chrome-router-")
        driver = _abre_chrome(perfil, teste)
        fluxo(driver, cfg, not seco)
        if seco:
            _log("--seco ok: login e botao de reboot encontrados")
            return 0
        _marca_reinicio()
        _log("roteador reiniciado com sucesso")
        time.sleep(10)
        return 0
    except Exception as e:
        _log(f"Erro ao reiniciar o roteador: {e}")
        if driver is not None:
            _dump_pagina(driver)
        return -1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if perfil is not None:
            shutil.rmtree(perfil, ignore_errors=True)
        _solta_trava()


if __name__ == "__main__":
    import sys
    _args = sys.argv[1:]
    ECOAR = "--test" in _args or "--seco" in _args
    raise SystemExit(0 if restart_router(teste="--test" in _args,
                                         seco="--seco" in _args) == 0 else 1)
