"""Testes da caixa-preta da Rasp (deploy/rasp/caixa_preta.py).

Tudo roda sobre um /proc e um /sys falsos montados em tmp_path: o agente le
os arquivos por caminho configuravel justamente para isto. As linhas de
/proc/net/tcp e /proc/net/arp seguem o formato do kernel 6.6 da 102.
"""
import importlib.util
import json
import os
from pathlib import Path

CP_PATH = Path("deploy/rasp/caixa_preta.py")


def _mod():
    spec = importlib.util.spec_from_file_location("caixa_preta", CP_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cp = _mod()

# 192.168.11.102:41022 -> 192.168.10.101:55000, ESTABLISHED, 0x1F40 bytes na fila
TCP_HDR = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
           "retrnsmt   uid  timeout inode\n")
LINHA_CLIENTE = ("   0: 660BA8C0:A03E 650AA8C0:D6D8 01 00001F40:00000000 01:00000014 "
                 "00000003  1000        0 12345 1 0000000000000000 20 4 30 10 -1\n")
LINHA_OUTRA = ("   1: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 "
               "00000000     0        0 999 1 0000000000000000 100 0 0 10 0\n")
ARP = ("IP address       HW type     Flags       HW address            Mask     Device\n"
       "192.168.11.254   0x1         0x2         f8:80:96:05:99:73     *        eth0\n"
       "192.168.11.9     0x1         0x0         00:00:00:00:00:00     *        eth0\n")


def _proc(tmp_path, tcp=TCP_HDR + LINHA_CLIENTE + LINHA_OUTRA, arp=ARP):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("93c03fdc-ac06-4c47-a11f-afec8ee00891\n")
    (proc / "uptime").write_text("4710060.00 100.00\n")
    (proc / "net/tcp").write_text(tcp)
    (proc / "net/arp").write_text(arp)
    (proc / "net/dev").write_text(
        "Inter-|   Receive |  Transmit\n face |bytes packets errs drop fifo frame "
        "compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
        "  eth0: 1235012420 18095490 0 0 0 0 0 0 50155161370 34338829 0 0 0 0 0 0\n")
    (proc / "loadavg").write_text("0.26 0.20 0.13 1/300 2727233\n")
    (proc / "meminfo").write_text("MemTotal: 7900000 kB\nMemAvailable: 7300000 kB\n")
    (proc / "mounts").write_text("/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n")
    sysfs = tmp_path / "sys"
    (sysfs / "class/net/eth0").mkdir(parents=True)
    (sysfs / "class/net/eth0/carrier").write_text("1\n")
    (sysfs / "class/net/eth0/operstate").write_text("up\n")
    (sysfs / "class/thermal/thermal_zone0").mkdir(parents=True)
    (sysfs / "class/thermal/thermal_zone0/temp").write_text("45200\n")
    return proc, sysfs


def _cliente(proc, pid=2727233, inicio_ticks=470973000, nome="2026_02_05_Cliente_H01.py"):
    d = proc / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(
        b"python3\0/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/" + nome.encode() + b"\0")
    campos = ["S", "2727000"] + ["0"] * 15 + ["7", "0", str(inicio_ticks)] + ["0"] * 10
    (d / "stat").write_text(f"{pid} (python3) " + " ".join(campos) + "\n")
    (d / "statm").write_text("30000 33000 2000 1 0 20000 0\n")


# ------------------------------------------------------------- leitura ---

def test_proc_net_tcp_decodifica_ip_porta_estado_e_fila():
    c = cp.parse_proc_net_tcp(TCP_HDR + LINHA_CLIENTE)[0]
    assert c["local"] == "192.168.11.102:41022"
    assert (c["remoto_ip"], c["remoto_porta"]) == ("192.168.10.101", 55000)
    assert c["estado"] == "ESTABLISHED"
    assert c["txq"] == 8000 and c["rxq"] == 0 and c["retr"] == 3


def test_conexoes_servidor_filtra_destino_e_ignora_portas_das_sondas(tmp_path):
    proc, _ = _proc(tmp_path)
    assert len(cp.conexoes_servidor(str(proc), "192.168.10.101", 55000)) == 1
    assert cp.conexoes_servidor(str(proc), "192.168.10.101", 55000, [41022]) == []
    # sondas saem das portas 55090-55099 e nunca contam como conexao do cliente
    sonda = LINHA_CLIENTE.replace("660BA8C0:A03E", "660BA8C0:%04X" % 55095)
    (proc / "net/tcp").write_text(TCP_HDR + sonda)
    assert cp.conexoes_servidor(str(proc), "192.168.10.101", 55000) == []


def test_arp_distingue_completa_incompleta_e_ausente():
    assert cp.parse_arp(ARP, "192.168.11.254") == "ok"
    assert cp.parse_arp(ARP, "192.168.11.9") == "incompleta"
    assert cp.parse_arp(ARP, "192.168.11.1") is None


def test_interface_le_cabo_e_contadores(tmp_path):
    proc, sysfs = _proc(tmp_path)
    d = cp.interface(str(proc), str(sysfs), "eth0")
    assert d["carrier"] == "1" and d["operstate"] == "up"
    assert d["tx_bytes"] == 50155161370 and d["rx_bytes"] == 1235012420


def test_processo_do_cliente_com_idade_e_memoria(tmp_path):
    proc, _ = _proc(tmp_path)
    _cliente(proc)
    _cliente(proc, pid=4242, nome="caixa_preta.py")     # o proprio agente nao conta
    (proc / "77").mkdir()
    (proc / "77" / "cmdline").write_bytes(b"/usr/lib/chromium/chromium\0--headless\0")
    out = cp.processos_cliente(str(proc), "Cliente", eu=1)
    assert [c["pid"] for c in out] == [2727233]
    hz = os.sysconf("SC_CLK_TCK")
    assert out[0]["idade_s"] == round(4710060 - 470973000 / hz)
    assert out[0]["ppid"] == 2727000 and out[0]["estado"] == "S"
    assert out[0]["rss_mb"] > 0


def test_motivo_e_historico(tmp_path):
    base, lf = tmp_path / "base", tmp_path / "lf"
    base.mkdir()
    lf.mkdir()
    (base / "router.log").write_text("reinicializando roteador\n"
                                    "Erro ao reiniciar o roteador: Chrome failed to start\n")
    (lf / "falha_2026-07-15_03-32-36.txt").write_text(
        "Data/Hora da falha: 2026-07-15 03:32:36\nMotivo: timed out\n")
    h = cp.historico({"base": str(base), "logs_failure": str(lf)})
    assert h["router_log_fim"][-1].startswith("Erro ao reiniciar")
    assert h["logs_failure_recentes"][0]["motivo"] == "timed out"


# ------------------------------------------------------------ arquivos ---

def test_seguidor_comeca_do_fim_e_depois_so_entrega_linha_completa(tmp_path):
    log = tmp_path / "router.log"
    log.write_text("antiga\n")
    s = cp.Seguidor(str(log))
    assert s.novas() == []
    with open(log, "a") as f:
        f.write("nova 1\nmeia linha")
    assert s.novas() == ["nova 1"]
    with open(log, "a") as f:
        f.write(" completa\n")
    assert s.novas() == ["meia linha completa"]


def test_seguidor_recomeca_quando_o_arquivo_e_truncado(tmp_path):
    log = tmp_path / "router.log"
    log.write_text("um\ndois\ntres\n")
    s = cp.Seguidor(str(log))
    s.novas()
    log.write_text("depois do truncamento\n")
    assert s.novas() == ["depois do truncamento"]


def test_seguidor_retoma_do_ponto_salvo_entre_execucoes(tmp_path):
    log = tmp_path / "router.log"
    log.write_text("um\n")
    a = cp.Seguidor(str(log))
    a.novas()
    with open(log, "a") as f:
        f.write("enquanto o agente estava fora\n")
    b = cp.Seguidor(str(log), a.ino, a.pos)
    assert b.novas() == ["enquanto o agente estava fora"]


def test_vigia_falhas_nao_repete_o_passado_e_pega_o_novo(tmp_path):
    lf = tmp_path / "lf"
    lf.mkdir()
    (lf / "falha_2026-07-15_03-32-36.txt").write_text("Motivo: timed out\n")
    v = cp.VigiaFalhas(str(lf))
    assert v.novos() == []
    novo = lf / "falha_2026-09-04_09-00-00.txt"
    novo.write_text("Motivo: [Errno 32] Broken pipe\n")
    os.utime(lf, (1e9, 2e9))                 # garante mtime diferente
    assert v.novos() == [("falha_2026-09-04_09-00-00.txt", "[Errno 32] Broken pipe")]
    # nova execucao com o estado salvo: so o que for posterior ao ultimo nome
    w = cp.VigiaFalhas(str(lf), v.mtime, v.ultimo)
    (lf / "falha_2026-09-04_10-00-00.txt").write_text("Motivo: x\n")
    assert [n for n, _ in w.novos()] == ["falha_2026-09-04_10-00-00.txt"]


# ------------------------------------------------------------- registro ---

def test_registro_roda_e_mantem_so_os_mais_novos(tmp_path):
    r = cp.Registro(str(tmp_path), rotacao_mb=0, manter=2)
    r.limite = 200
    for i in range(30):
        r.grava({"i": i, "x": "y" * 50})
    rodados = list(tmp_path.glob("cp-*.jsonl"))
    assert 1 <= len(rodados) <= 2
    linhas = [json.loads(x) for p in rodados + [tmp_path / "atual.jsonl"] if p.exists()
              for x in p.read_text().splitlines()]
    assert max(x["i"] for x in linhas) == 29


# --------------------------------------------------------------- agente ---

def _agente(tmp_path, monkeypatch, proc, sysfs):
    base, lf = tmp_path / "base", tmp_path / "lf"
    base.mkdir(exist_ok=True)
    lf.mkdir(exist_ok=True)
    (base / "router.log").write_text("reinicializando roteador\n")
    cfg = dict(cp.PADRAO, camera="102", wan="192.168.10.102", base=str(base),
               logs_failure=str(lf), saida=str(tmp_path / "out"),
               estado=str(tmp_path / "estado.json"), proc=str(proc), sys=str(sysfs))
    respostas = {"gw": 1.0}
    monkeypatch.setattr(cp, "ping_ms", lambda ip, timeout=2.0: respostas.get(
        "gw") if ip == cfg["gateway"] else 50.0)
    monkeypatch.setattr(cp, "tcp", lambda *a, **k: ("aberta", 3))
    relogio = {"t": 1.0e9 - 40 * 86400, "m": 100.0}
    # o CPE tem a hora certa; a Pi esta 40 dias atrasada
    monkeypatch.setattr(cp, "http_data", lambda ip: (200, relogio["t"] + 40 * 86400))
    monkeypatch.setattr(cp, "throttled", lambda *a, **k: "0x0")
    ag = cp.Agente(cfg, agora=lambda: relogio["t"], mono=lambda: relogio["m"])
    return ag, cfg, respostas, relogio


def _eventos(cfg):
    p = Path(cfg["saida"]) / "atual.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if '"evento"' in x]


def test_agente_registra_historico_estado_inicial_e_relogio(tmp_path, monkeypatch):
    proc, sysfs = _proc(tmp_path)
    _cliente(proc)
    ag, cfg, _, _ = _agente(tmp_path, monkeypatch, proc, sysfs)
    ag.inicio()
    ag.ciclo()
    evs = {e["ev"]: e for e in _eventos(cfg)}
    assert evs["historico"]["router_log_fim"] == ["reinicializando roteador"]
    assert evs["estado_inicial"]["conn"][0]["estado"] == "ESTABLISHED"
    assert evs["estado_inicial"]["cliente"][0]["pid"] == 2727233
    assert evs["relogio"]["offset_s"] == 40 * 86400


def test_agente_registra_transicoes(tmp_path, monkeypatch):
    proc, sysfs = _proc(tmp_path)
    _cliente(proc)
    ag, cfg, respostas, relogio = _agente(tmp_path, monkeypatch, proc, sysfs)
    ag.inicio()
    ag.ciclo()
    # o gateway some, a conexao fecha, o cliente morre e uma linha entra no router.log
    respostas["gw"] = None
    (proc / "net/tcp").write_text(TCP_HDR + LINHA_OUTRA)
    for f in (proc / "2727233").iterdir():
        f.unlink()
    (proc / "2727233").rmdir()
    with open(Path(cfg["base"]) / "router.log", "a") as f:
        f.write("2026-09-04 08:00:00 | Erro ao reiniciar o roteador: Chrome failed to start\n")
    relogio["t"] += 30
    relogio["m"] += 30
    ag.ciclo()
    nomes = [e["ev"] for e in _eventos(cfg)]
    for esperado in ("gateway_perdido", "conexao_servidor", "cliente_sumiu", "router_log"):
        assert esperado in nomes, esperado


def test_agente_avisa_fila_presa(tmp_path, monkeypatch):
    proc, sysfs = _proc(tmp_path)
    ag, cfg, _, relogio = _agente(tmp_path, monkeypatch, proc, sysfs)
    ag.inicio()
    for _ in range(cp.CICLOS_FILA_PRESA + 2):     # bytes na fila, contador da placa parado
        relogio["m"] += 30
        ag.ciclo()
    assert [e["ev"] for e in _eventos(cfg)].count("fila_presa") == 1


def test_boot_novo_e_detectado_pelo_estado_salvo(tmp_path, monkeypatch):
    proc, sysfs = _proc(tmp_path)
    ag, cfg, _, _ = _agente(tmp_path, monkeypatch, proc, sysfs)
    ag.inicio()
    ag.ciclo()
    (proc / "sys/kernel/random/boot_id").write_text("11111111-2222-3333-4444-555555555555\n")
    ag2 = cp.Agente(cfg, agora=lambda: 1.0e9, mono=lambda: 5.0)
    ag2.inicio()
    assert "boot_novo" in [e["ev"] for e in _eventos(cfg)]


def test_relatorio_mostra_eventos_lacuna_e_hora_real(tmp_path, monkeypatch, capsys):
    proc, sysfs = _proc(tmp_path)
    ag, cfg, _, relogio = _agente(tmp_path, monkeypatch, proc, sysfs)
    ag.inicio()
    ag.ciclo()
    relogio["t"] += 600
    relogio["m"] += 600                     # 10 min sem amostra no mesmo boot
    ag.ciclo()
    cp.relatorio(cfg["saida"])
    out = capsys.readouterr().out
    assert "LACUNA de 600 s" in out
    assert "+40.00 dias" in out
    assert "estado_inicial" in out


def test_tcp_sai_da_porta_local_pedida_e_distingue_recusa():
    import socket as so
    srv = so.socket()
    srv.setsockopt(so.SOL_SOCKET, so.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta = srv.getsockname()[1]
    r, _ = cp.tcp("127.0.0.1", porta, 2, porta_local=55093)
    c, origem = srv.accept()
    assert r == "aberta" and origem[1] == 55093
    c.close()
    srv.close()
    assert cp.tcp("127.0.0.1", porta, 2)[0] == "recusada"
