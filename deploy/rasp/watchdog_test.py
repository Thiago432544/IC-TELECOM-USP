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
