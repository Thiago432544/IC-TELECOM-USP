"""Testes do coletor de radio da Rasp (deploy/rasp/radio_log.py).

O JSON de referencia e' a leitura real do RUT200 da 105 em 28/08/2026 - com os
campos que o modulo devolve como 'N/A' em LTE preservados, porque foi neles que
o parser podia quebrar.
"""
import importlib.util
import time
from pathlib import Path

import pytest

RL_PATH = Path("deploy/rasp/radio_log.py")


def _mod():
    spec = importlib.util.spec_from_file_location("radio_log", RL_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rl = _mod()


STATUS_REAL = {"success": True, "data": [{
    "band": "LTE B28", "cellid": "139969826", "conntype": "4G (LTE)",
    "ecio": "N/A", "iccid": "8955130350204626872F", "id": "1-1",
    "imei": "866496072334447", "imsi": "724130310462647",
    "manufacturer": "Quectel", "model": "EC200A-AU",
    "netstate": "Registered, home", "ntype": "LTE", "operator": "NLT",
    "rscp": "N/A", "rsrp": -45, "rsrq": -7, "rssi": -47, "signal": -47,
    "sinr": 35, "state": "Connected", "tac": "1", "temperature": 40,
    "rxbytes": 3179635202.0, "txbytes": 141177094170.0,
    "cell_info": [{"lac": "N/A", "mcc": "724", "rsrp": "N/A", "ue_state": 3,
                   "bandwidth": "5", "sinr": "N/A", "rsrq": "N/A",
                   "cellid": "139969826", "mnc": "13", "earfcn": 9235,
                   "pcid": 326, "tac": "1"}],
}]}


def test_extrai_os_numeros_que_importam():
    linha = rl.extrai(STATUS_REAL)
    assert linha["rsrp"] == -45.0
    assert linha["rsrq"] == -7.0
    assert linha["sinr"] == 35.0
    assert linha["band"] == "LTE B28"
    assert linha["netstate"] == "Registered, home"


def test_pega_o_topo_e_nao_o_cell_info():
    """O cell_info traz rsrp/rsrq como 'N/A' enquanto o topo tem o valor -
    ler do lugar errado daria uma serie inteira vazia."""
    assert rl.extrai(STATUS_REAL)["rsrp"] == -45.0


def test_earfcn_e_pcid_vem_do_cell_info():
    """Sao os campos que cruzam com a eNodeB, e so existem la dentro."""
    linha = rl.extrai(STATUS_REAL)
    assert linha["earfcn"] == 9235 and linha["pcid"] == 326


def test_na_vira_vazio_e_nao_texto():
    """Gravar a string 'N/A' num campo numerico estraga a serie."""
    fora = {"success": True, "data": [{"rsrp": "N/A", "rsrq": "N/A",
                                       "sinr": "N/A", "rssi": "N/A",
                                       "netstate": "Not registered"}]}
    linha = rl.extrai(fora)
    assert linha["rsrp"] is None and linha["rsrq"] is None
    assert linha["netstate"] == "Not registered"


def test_resposta_vazia_nao_levanta():
    linha = rl.extrai({"success": True, "data": []})
    assert linha["rsrp"] is None and linha["ts"]


def test_login_uma_vez_so_enquanto_o_token_vale():
    logins = []
    def post(url, corpo, cab=None, timeout=10):
        logins.append(url)
        return {"success": True, "data": {"token": "T1", "expires": 299}}
    def get(url, cab=None, timeout=10):
        assert cab["Authorization"] == "Bearer T1"
        return STATUS_REAL
    r = rl.Radio({"url_api": "http://r", "usuario": "u", "senha": "s"},
                 post=post, get=get, agora=lambda: 1000.0)
    r.amostra(); r.amostra(); r.amostra()
    assert len(logins) == 1


def test_renova_o_token_antes_de_vencer():
    """299 s de validade e amostra de minuto em minuto: sem margem, uma amostra
    cai bem na virada - e a virada costuma ser quando o enlace esta ruim."""
    logins = []
    relogio = {"t": 1000.0}
    def post(url, corpo, cab=None, timeout=10):
        logins.append(relogio["t"])
        return {"success": True, "data": {"token": "T", "expires": 299}}
    r = rl.Radio({"url_api": "http://r", "usuario": "u", "senha": "s"},
                 post=post, get=lambda *a, **k: STATUS_REAL,
                 agora=lambda: relogio["t"])
    r.amostra()
    relogio["t"] += 200          # ainda vale
    r.amostra()
    relogio["t"] += 50           # passou de 299-60: renova
    r.amostra()
    assert len(logins) == 2


def test_token_recusado_tenta_de_novo_uma_vez():
    tentativas = []
    def get(url, cab=None, timeout=10):
        tentativas.append(cab["Authorization"])
        if len(tentativas) == 1:
            return {"success": False, "errors": [{"error": "Unauthorized"}]}
        return STATUS_REAL
    r = rl.Radio({"url_api": "http://r", "usuario": "u", "senha": "s"},
                 post=lambda *a, **k: {"success": True,
                                       "data": {"token": "T", "expires": 299}},
                 get=get, agora=lambda: 1000.0)
    assert r.amostra()["rsrp"] == -45.0
    assert len(tentativas) == 2


def test_login_recusado_diz_o_motivo():
    r = rl.Radio({"url_api": "http://r", "usuario": "u", "senha": "errada"},
                 post=lambda *a, **k: {"success": False,
                                       "errors": [{"error": "Unauthorized"}]},
                 get=lambda *a, **k: STATUS_REAL)
    with pytest.raises(RuntimeError, match="login recusado"):
        r.amostra()


def test_conf_le_o_mesmo_arquivo_do_watchdog(tmp_path):
    p = tmp_path / "router.conf"
    p.write_text("modelo = teltonika\n"
                 "usuario = admin\n"
                 "senha = segredo   # comentario\n")
    cfg = rl.le_conf(str(p))
    assert cfg["usuario"] == "admin" and cfg["senha"] == "segredo"
    assert cfg["url_api"] == "http://192.168.11.1"


def test_conf_ausente_nao_levanta():
    cfg = rl.le_conf("/nao/existe/router.conf")
    assert cfg["senha"] == ""


def test_csv_ganha_cabecalho_uma_vez_so(tmp_path):
    alvo = str(tmp_path / "radio.csv")
    linha = rl.extrai(STATUS_REAL)
    rl.grava(linha, alvo); rl.grava(linha, alvo)
    texto = Path(alvo).read_text().splitlines()
    assert texto[0].startswith("ts,rsrp,rsrq")
    assert len(texto) == 3
