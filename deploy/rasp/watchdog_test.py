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

Instalar em: /home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/watchdog_test.py
Configuracao opcional (fora do git, ao lado do script): router.conf
    modelo = elsys | teltonika
    url    = http://192.168.11.254/index.html?status
    usuario = admin
    senha   = ...
"""
import os
import shutil
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
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

ELSYS_BTN_LOGIN = "/html/body/div[1]/div[2]/form/div[1]/div[3]/input"
ELSYS_BTN_REBOOT = ("/html/body/div[1]/div[2]/form/div[5]/div[2]/table/tbody"
                    "/tr[7]/td[2]/input")
TELT_USUARIO = '//*[@id="username"]'
TELT_SENHA = '//*[@id="password"]'
TELT_BTN_ENTRAR = "/html/body/div/div/div[1]/form/button"
TELT_BTN_REBOOT = ("/html/body/div/div/div[2]/div[2]/div/div/div/div/div"
                   "/button")

PADRAO = {"modelo": "elsys",
          "url": "http://192.168.11.254/index.html?status",
          "usuario": "",
          "senha": ""}


def _log(msg):
    """Nunca pode derrubar a funcao: o log e' conveniencia, o reinicio nao."""
    try:
        with open(LOG, "a") as f:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}", file=f)
    except OSError:
        pass


def _config():
    """router.conf e' opcional; ausente, vale o padrao (Elsys, sem login)."""
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


def _abre_chrome(perfil):
    options = Options()
    options.binary_location = CHROMIUM
    for flag in FLAGS:
        options.add_argument(flag)
    # Perfil proprio por chamada: e' o que impede duas chamadas simultaneas de
    # brigarem pelo diretorio default e produzirem DevToolsActivePort.
    options.add_argument(f"--user-data-dir={perfil}")
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)


def _fluxo_elsys(driver, cfg):
    driver.get(cfg["url"])
    time.sleep(10)
    driver.find_element(By.XPATH, ELSYS_BTN_LOGIN).click()
    time.sleep(10)
    driver.find_element(By.XPATH, ELSYS_BTN_REBOOT).click()


def _fluxo_teltonika(driver, cfg):
    driver.get(cfg["url"])
    time.sleep(10)
    driver.find_element(By.XPATH, TELT_USUARIO).send_keys(cfg["usuario"])
    driver.find_element(By.XPATH, TELT_SENHA).send_keys(cfg["senha"])
    driver.find_element(By.XPATH, TELT_BTN_ENTRAR).click()
    time.sleep(10)
    driver.get(cfg.get("url_reboot", "http://192.168.11.1/status/network/mobile"))
    time.sleep(10)
    driver.find_element(By.XPATH, TELT_BTN_REBOOT).click()


FLUXOS = {"elsys": _fluxo_elsys, "teltonika": _fluxo_teltonika}


def restart_router():
    """Reinicia o CPE. 0 = reiniciou, -1 = tentou e falhou, -2 = nao tentou.

    Nunca levanta excecao: quem chama esta dentro de um `except` e morreria.
    """
    if _cedo_demais():
        _log("pulando: CPE reiniciado ha menos de "
             f"{MIN_INTERVALO_S}s, deixando ele subir")
        return -2
    if not _pega_trava():
        _log("pulando: ja existe um reinicio em andamento")
        return -2

    _log("reinicializando roteador")
    cfg = _config()
    fluxo = FLUXOS.get(cfg["modelo"], _fluxo_elsys)
    driver = None
    perfil = None
    try:
        perfil = tempfile.mkdtemp(prefix="chrome-router-")
        driver = _abre_chrome(perfil)
        fluxo(driver, cfg)
        _marca_reinicio()
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
        if perfil is not None:
            shutil.rmtree(perfil, ignore_errors=True)
        _solta_trava()


if __name__ == "__main__":
    raise SystemExit(restart_router())
