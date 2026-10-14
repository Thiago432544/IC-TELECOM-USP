"""Testes do auto-reparo da Rasp (deploy/rasp/auto_reparo.py).

A logica de decisao roda contra um relogio falso, uma sonda falsa e um
executor de comandos que so anota o que teria rodado: nenhum teste derruba
interface nem chama nmcli de verdade. O /proc e o /sys sao montados em
tmp_path, como nos testes da caixa-preta.
"""
import importlib.util
import json
import sys
from pathlib import Path

RASP = Path("deploy/rasp")


def _mod(nome):
    spec = importlib.util.spec_from_file_location(nome, RASP / f"{nome}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[nome] = m
    spec.loader.exec_module(m)
    return m


cp = _mod("caixa_preta")
ar = _mod("auto_reparo")

TCP_HDR = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
           "retrnsmt   uid  timeout inode\n")
CLIENTE_CONECTADO = ("   0: 660BA8C0:A03E 650AA8C0:D6D8 01 00000000:00000000 01:00000014 "
                     "00000000  1000        0 12345 1 0000000000000000 20 4 30 10 -1\n")


class Cena:
    """Relogio, sonda, comandos e /proc/sys falsos de um teste."""

    def __init__(self, tmp_path, uptime=100000.0, carrier="1", modo="ativo"):
        self.t, self.m = 1.7e9, 1000.0
        self.resposta = "aberta"
        self.cmds = []
        self.proc = tmp_path / "proc"
        self.sys = tmp_path / "sys"
        (self.proc / "net").mkdir(parents=True)
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text("93c03fdc-ac06\n")
        (self.proc / "uptime").write_text(f"{uptime} 1.0\n")
        (self.proc / "net/tcp").write_text(TCP_HDR)
        (self.proc / "net/dev").write_text("a\nb\n  eth0: 1 2 0 0 0 0 0 0 3 4 0 0 0 0 0 0\n")
        eth = self.sys / "class/net/eth0"
        eth.mkdir(parents=True)
        (eth / "carrier").write_text(carrier + "\n")
        (eth / "operstate").write_text("up\n")
        (eth / "flags").write_text("0x1003\n")
        self.cfg = dict(ar.PADRAO, camera="102", modo=modo, proc=str(self.proc),
                        sys=str(self.sys), saida=str(tmp_path / "out"),
                        estado=str(tmp_path / "estado.json"),
                        pausa=str(tmp_path / "pausa"), conexao_nm="Ethernet connection 1")
        self.ag = self.novo_agente()

    def novo_agente(self):
        return ar.AutoReparo(self.cfg, agora=lambda: self.t, mono=lambda: self.m,
                             run=self.run, dorme=self.dorme, sonda=self.sonda)

    def sonda(self, ip, porta, timeout, porta_local=None):
        assert porta_local in ar.PORTAS_AUTO_REPARO
        return self.resposta, 5

    def run(self, cmd, timeout=60):
        self.cmds.append(cmd)
        if cmd[:2] == ["systemctl", "is-active"]:
            return 0, "active"
        return 0, ""

    def dorme(self, s):
        self.cmds.append(["sleep", s])

    def minutos(self, n, resposta=None):
        """n ciclos de 1 min com a sonda dando `resposta`."""
        if resposta is not None:
            self.resposta = resposta
        for _ in range(n):
            self.t += 60
            self.m += 60
            self.ag.passo()

    def eventos(self):
        p = Path(self.cfg["saida"]) / "atual.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def acoes_de_rede(self):
        return [c for c in self.cmds if c[0] in ("nmcli", "ip")]


def test_caminho_bom_nao_faz_nada(tmp_path):
    c = Cena(tmp_path)
    c.minutos(60, "aberta")
    assert c.acoes_de_rede() == []


def test_cliente_conectado_dispensa_a_sonda(tmp_path):
    c = Cena(tmp_path)
    (c.proc / "net/tcp").write_text(TCP_HDR + CLIENTE_CONECTADO)
    c.resposta = "timeout"                    # se sondasse, daria ruim
    assert c.ag.caminho() == ("bom", "cliente_conectado")


def test_escada_renegocia_em_10_min_e_derruba_o_link_em_20(tmp_path):
    c = Cena(tmp_path)
    c.minutos(10, "timeout")
    assert c.acoes_de_rede() == [["nmcli", "connection", "up", "Ethernet connection 1"]]
    c.minutos(10)
    rede = c.acoes_de_rede()
    assert rede[1:] == [["ip", "link", "set", "eth0", "down"], ["ip", "link", "set", "eth0", "up"]]
    # o link ficou fora o tempo configurado, entre o down e o up
    i = c.cmds.index(["ip", "link", "set", "eth0", "down"])
    assert c.cmds[i + 1] == ["sleep", 15]
    nomes = [e["ev"] for e in c.eventos()]
    assert nomes == ["caminho_ruim", "reparo", "reparo"]


def test_recusa_conta_como_caminho_ruim(tmp_path):
    """O servidor sempre escuta na 55000: RST vem de alguem no meio (a Elsys, em 24/08)."""
    c = Cena(tmp_path)
    c.minutos(10, "recusada")
    assert c.acoes_de_rede()


def test_volta_registra_qual_degrau_resolveu(tmp_path):
    c = Cena(tmp_path)
    c.minutos(10, "timeout")               # degrau 1
    c.minutos(2, "aberta")
    ok = [e for e in c.eventos() if e["ev"] == "caminho_ok"][0]
    assert ok["resolvido_por"] == 1
    assert ok["fora_s"] == 660 and ok["desde_ultimo_reparo_s"] == 60


def test_nova_rodada_so_depois_da_espera_e_respeita_o_limite_diario(tmp_path):
    c = Cena(tmp_path)
    c.cfg["max_acoes_dia"] = 3
    c.ag = c.novo_agente()
    c.minutos(20, "timeout")                # rodada 0: degraus 1 e 2
    assert len([x for x in c.acoes_de_rede() if x[0] == "nmcli"]) == 1
    c.minutos(59)
    assert len([x for x in c.acoes_de_rede() if x[0] == "nmcli"]) == 1
    c.minutos(1)                            # 60 min depois do degrau 2: rodada 1
    assert len([x for x in c.acoes_de_rede() if x[0] == "nmcli"]) == 2
    c.minutos(10)                           # o degrau 2 da rodada 1 bate no limite (3)
    assert c.acoes_de_rede().count(["ip", "link", "set", "eth0", "down"]) == 1
    puladas = [e for e in c.eventos() if e["ev"] == "reparo_pulado"]
    assert len(puladas) == 1 and puladas[0]["motivo"] == "limite_diario"


def test_cabo_desligado_nao_age(tmp_path):
    c = Cena(tmp_path, carrier="0")
    c.minutos(30, "timeout")
    assert c.acoes_de_rede() == []
    assert [e["motivo"] for e in c.eventos() if e["ev"] == "reparo_pulado"] == ["cabo_desligado"]


def test_logo_depois_do_boot_nao_age(tmp_path):
    c = Cena(tmp_path, uptime=120.0)
    c.minutos(30, "timeout")
    assert c.acoes_de_rede() == [] and c.eventos() == []


def test_modo_observar_so_registra(tmp_path):
    c = Cena(tmp_path, modo="observar")
    c.minutos(20, "timeout")
    assert c.acoes_de_rede() == []
    assert [e["ev"] for e in c.eventos()].count("reparo_teria_agido") == 2


def test_pausa_suspende_e_retoma(tmp_path):
    c = Cena(tmp_path)
    Path(c.cfg["pausa"]).write_text("")
    c.minutos(30, "timeout")
    assert c.acoes_de_rede() == []
    Path(c.cfg["pausa"]).unlink()
    c.minutos(1)
    assert [e["ev"] for e in c.eventos()][:2] == ["pausado", "retomado"]


def test_derruba_e_levanta_sempre_levanta():
    feitos = []

    def run(cmd, timeout=60):
        feitos.append(cmd[-1])
        return (1 if cmd[-1] == "up" and feitos.count("up") < 2 else 0), ""

    def dorme(s):
        if s == 15:
            raise KeyboardInterrupt          # o processo morre no meio do degrau
    try:
        ar.derruba_e_levanta("eth0", 15, run, dorme)
    except KeyboardInterrupt:
        pass
    assert feitos == ["down", "up", "up"]   # tentou de novo ate subir


def test_partida_religa_interface_desligada(tmp_path):
    c = Cena(tmp_path)
    (c.sys / "class/net/eth0/flags").write_text("0x1002\n")
    c.ag.garante_interface_ligada()
    assert ["ip", "link", "set", "eth0", "up"] in c.cmds


def test_vnc_parado_e_reiniciado_so_se_estiver_habilitado(tmp_path):
    c = Cena(tmp_path)

    def run(cmd, timeout=60):
        c.cmds.append(cmd)
        if cmd[:2] == ["systemctl", "is-active"]:
            return 3, "inactive"
        if cmd[:2] == ["systemctl", "is-enabled"]:
            return 0, "enabled"
        return 0, ""
    c.ag.run = run
    c.minutos(1, "aberta")
    assert ["systemctl", "restart", "vncserver-x11-serviced"] in c.cmds
    assert "vnc_reiniciado" in [e["ev"] for e in c.eventos()]


def test_nome_da_conexao_do_networkmanager():
    saida = "Wired connection 1:eth0\nlo:lo\nnome\\:com\\:dois-pontos:wlan0\n"
    run = lambda cmd, timeout=60: (0, saida)        # noqa: E731
    assert ar.conexao_nm("eth0", run) == "Wired connection 1"
    assert ar.conexao_nm("wlan0", run) == "nome:com:dois-pontos"
    assert ar.conexao_nm("eth1", run) is None


def test_eventos_aparecem_no_relatorio_da_caixa_preta(tmp_path, capsys):
    c = Cena(tmp_path)
    c.minutos(10, "timeout")
    cp.relatorio(c.cfg["saida"])
    out = capsys.readouterr().out
    assert "caminho_ruim" in out and "renegociar_endereco" in out
