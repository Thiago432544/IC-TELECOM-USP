"""Testes da leitura do radio pelo monitor (monitor/radio.py)."""
import time

from monitor.config import RadioCfg
from monitor.radio import RadioReader, parse_csv

CABECALHO = ("ts,rsrp,rsrq,sinr,rssi,band,earfcn,pcid,cellid,tac,"
             "temperatura,netstate,conntype,rxbytes,txbytes")


def _linha(ts, rsrp="-50.0", rsrq="-7.0", sinr="35.0"):
    return (f"{ts},{rsrp},{rsrq},{sinr},-47.0,LTE B28,9235,326,139969826,1,"
            f"40.0,\"Registered, home\",4G (LTE),3179635202.0,141177094170.0")


def _agora(delta=0.0):
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() + delta))


def _cfg(**kw):
    base = dict(enabled=True, interval_s=60, ssh_user="pi",
                csv="/home/pi/radio.csv", max_atraso_s=600,
                cameras={"105": "192.168.10.105"})
    base.update(kw)
    return RadioCfg(**base)


# -- parse ---------------------------------------------------------------
def test_le_os_tres_numeros():
    l = parse_csv(CABECALHO + "\n" + _linha("2026-08-30 15:07:39"))
    assert (l.rsrp, l.rsrq, l.sinr) == (-50.0, -7.0, 35.0)
    assert l.band == "LTE B28" and l.netstate == "Registered, home"


def test_le_por_nome_de_coluna_e_nao_por_posicao():
    """Se o coletor da Pi ganhar um campo novo, o monitor tem de continuar
    lendo os certos em vez de deslocar a linha inteira."""
    cab = "ts,extra,rsrp,rsrq,sinr,netstate"
    linha = "2026-08-30 15:07:39,zzz,-50.0,-7.0,35.0,Registered"
    l = parse_csv(cab + "\n" + linha)
    assert (l.rsrp, l.rsrq, l.sinr) == (-50.0, -7.0, 35.0)


def test_csv_so_com_cabecalho_nao_vira_amostra():
    """tail -1 de um CSV recem-criado devolve o proprio cabecalho."""
    assert parse_csv(CABECALHO + "\n" + CABECALHO) is None


def test_vazio_e_lixo_nao_levantam():
    assert parse_csv("") is None
    assert parse_csv("uma linha so") is None
    assert parse_csv(CABECALHO + "\nsem,data,nenhuma") is None


def test_na_do_modem_vira_vazio():
    l = parse_csv(CABECALHO + "\n" + _linha(_agora(), rsrp="", rsrq="", sinr=""))
    assert l is not None and l.rsrp is None and l.rsrq is None


# -- leitura -------------------------------------------------------------
def test_leitura_recente_passa():
    r = RadioReader(_cfg(), corre=lambda u, h, c: CABECALHO + "\n" + _linha(_agora(-30)))
    assert r.ler("192.168.10.105", time.time()).rsrp == -50.0


def test_amostra_velha_nao_entra():
    """Cron morto na Pi: tail -1 devolve a mesma linha para sempre. Mostrar o
    RSRP da semana passada como atual e' pior que nao mostrar nada."""
    r = RadioReader(_cfg(), corre=lambda u, h, c: CABECALHO + "\n" + _linha(_agora(-4000)))
    assert r.ler("192.168.10.105", time.time()) is None


def test_ssh_que_falha_nao_levanta():
    def explode(u, h, c):
        raise OSError("ssh nao encontrado")
    assert RadioReader(_cfg(), corre=explode).ler("h", time.time()) is None


def test_ssh_sem_saida_nao_levanta():
    assert RadioReader(_cfg(), corre=lambda u, h, c: "").ler("h", time.time()) is None


def test_usa_o_usuario_e_o_caminho_do_config():
    visto = {}
    def corre(u, h, c):
        visto.update(user=u, host=h, cmd=c)
        return CABECALHO + "\n" + _linha(_agora())
    RadioReader(_cfg(ssh_user="cilip", csv="/tmp/r.csv"),
                corre=corre).ler("10.0.0.9", time.time())
    assert visto["user"] == "cilip" and visto["host"] == "10.0.0.9"
    assert "head -n 1 /tmp/r.csv" in visto["cmd"]
    assert "tail -n 1 /tmp/r.csv" in visto["cmd"]


# -- config --------------------------------------------------------------
def test_radio_desligado_por_padrao():
    """O config.toml que ja roda no PC do SPA nao tem [radio]."""
    from pathlib import Path
    from monitor.config import load_config
    cfg = load_config(Path("config.example.toml"))
    assert cfg.radio.enabled is False
    assert cfg.radio.ssh_user == "pi"
