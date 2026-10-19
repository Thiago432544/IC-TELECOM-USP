"""Caixa-preta da camera: registra, de dentro da Pi, o que explica a proxima queda.

Por que existe. Em 14/10/2026 a 102 voltou depois de 50 dias fora, e so ai foi
possivel ler o que aconteceu: a Pi ficou ligada o tempo todo (54 dias de
uptime), e a ultima linha do router.log era o Chrome falhando ao abrir - o F7.
Mas o que faltou para fechar o caso foi exatamente o que ninguem registrava:

- o erro do cliente quando ele morre (ele roda num terminal, e o erro some);
- a hora de cada coisa (o router.log antigo nao tem hora, e o relogio da Pi
  esta ~40 dias atrasado);
- o estado da rede vista de dentro: o gateway responde? a conexao com o
  servidor esta aberta? os bytes estao saindo ou empilhados na fila?

Este agente registra isso a cada 30 s numa pasta propria, sem tocar no cliente.

O que e' gravado (JSON, uma linha por registro, em /var/log/caixa-preta/):

- "amostra" a cada ciclo: gateway (ping e ARP), WAN do CPE, porta 80 do CPE,
  cabo (carrier), bytes da eth0, conexoes com o servidor :55000 com a fila de
  envio e as retransmissoes, processos do cliente (PID, idade, memoria),
  throttled, temperatura, carga, memoria, disco, NTP.
- "evento" so quando algo muda: gateway perdido/voltou, conexao abriu/fechou,
  cliente novo/sumiu, fila de envio presa, linha nova no router.log, arquivo
  novo em logs_failure (com o Motivo), boot novo, relogio (Date do CPE).

Cada linha carrega o relogio da Pi (t), o monotonico (mono) e o boot_id. O
relogio da Pi esta errado em duas das tres cameras. O cabecalho Date do CPE
fica registrado a cada 10 min (evento "relogio"), mas a Elsys tambem nao tem
hora certa: a rede da NLT nao tem internet e o CPE nunca acerta o relogio (em
14/10/2026 o da 102 dizia 31/07/2023 e voltou 13,6 h ao reiniciar; com o
firmware de 16/10 passou a dizer marco de 2026). O relatorio nunca usa esse
relogio - um filtro de data plausivel ja foi enganado uma vez. O evento fica
porque os saltos dele denunciam reinicios da Elsys. A referencia confiavel e'
o PC do SPA: compare a hora da Pi de uma conexao aberta com o CONNECT do
LOG_connections e passe a diferenca ao relatorio com --offset.

Principio da spec: o monitoramento nunca pode causar a falha. Por isso: so
stdlib, somente leitura sobre tudo do cliente, nenhuma conexao com o servidor
enquanto o cliente esta conectado, e falha propria vira evento "erro" - o laco
nunca para.

Uso:
    sudo python3 caixa_preta.py --camera 102            # laco (o servico)
    sudo python3 caixa_preta.py --camera 102 --uma      # um ciclo, imprime e sai
    python3 caixa_preta.py --relatorio                  # linha do tempo dos eventos
    python3 caixa_preta.py --relatorio --horas 6        # so as ultimas 6 h
    python3 caixa_preta.py --relatorio --offset 40d1h4m # hora real = Pi + offset
    python3 caixa_preta.py --relatorio --offset "2026-09-04 21:29:58=2026-10-14 22:34:03"
    python3 caixa_preta.py --simples --minutos 30       # em frases, ultimos 30 min
    python3 caixa_preta.py --radio                      # le o sinal da Elsys uma vez
    python3 caixa_preta.py --quedas --horas 24          # RSRP em volta de cada queda

Radio (17/10/2026; DESLIGADO por padrao desde 19/10, ver PADRAO["radio_s"]):
a cada `radio_s` segundos le RSRP e RSRQ da pagina de status
da Elsys, pela LAN - funciona com o LTE fora, que e' quando importa. O firmware
novo pede login (formulario com sha256 do usuario e da senha); a senha sai do
mesmo router.conf do watchdog_test.py, nunca daqui. Cada leitura vai para
radio.jsonl, separado da linha do tempo; nela entram so os eventos radio_caiu /
radio_voltou (RSRP 10 dB abaixo da linha de base) e falhas de leitura. Enquanto
o watchdog reinicia a Elsys (router.lock), a leitura para, para nao disputar a
sessao. --quedas responde a pergunta de 17/10: o sinal caiu antes da queda
(obstaculo) ou estava bom e o trafego parou mesmo assim (a Elsys)?
"""
import argparse
import base64
import email.utils
import glob
import hashlib
import http.client
import http.cookiejar
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

VERSAO = "2026-10-19c"

PADRAO = {
    "camera": "",
    "gateway": "192.168.11.254",      # LAN do CPE (Elsys); Teltonika: 192.168.11.1
    "wan": "",                         # WAN do proprio CPE; vazio = 192.168.10.<camera>
    "servidor": "192.168.10.101",
    "porta": 55000,
    "intervalo": 30,
    "base": "/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL",
    "logs_failure": "/home/pi/Desktop/logs_failure",
    "saida": "/var/log/caixa-preta",
    "estado": "/var/lib/caixa-preta/estado.json",
    "proc": "/proc",
    "sys": "/sys",
    "iface": "eth0",
    "padrao_cliente": "Cliente",
    "rotacao_mb": 20,
    "manter_arquivos": 100,
    # Desligado por padrao (19/10). Na 102, 47 min depois de a leitura a cada
    # 5 s comecar (17/10 12:43), a pagina da Elsys parou de responder (13:30) e
    # nao voltou; quando o LTE caiu (18/10 03:38) o watchdog nao conseguiu o
    # login para reinicia-la, ~65 vezes, e a queda durou 30 h. Nao esta provado
    # que a leitura travou a pagina, mas o watchdog depende dela: so ligar
    # (radio_s = 300, por ex.) numa Elsys de bancada ou depois de testada la.
    "radio_s": 0,                      # 0 desliga a leitura do sinal da Elsys
    "radio_url": "",                   # vazio = http://<gateway>/status.html
    "router_conf": "",                 # vazio = <base>/router.conf (o do watchdog)
}
CONF = "/etc/caixa-preta.conf"

# Cadencias, em segundos. O CPE e' o equipamento suspeito: sonda-lo de leve.
CADA_CPE_TCP = 60
CADA_CPE_HTTP = 600
CADA_SERVIDOR = 300            # so quando o cliente NAO esta conectado
CADA_VCGENCMD = 300
CICLOS_FILA_PRESA = 4          # 4 x 30 s com bytes parados na fila de envio

# As sondas ao servidor saem de portas locais fixas, para nunca aparecerem como
# conexao do cliente: 55090-55094 sao desta caixa-preta, 55095-55099 do
# auto_reparo.py. Cada processo roda as suas, e assim a mesma porta so volta a
# ser usada depois que a anterior saiu do TIME_WAIT (60 s).
PORTAS_SONDA = range(55090, 55100)
PORTAS_CAIXA_PRETA = (55090, 55091, 55092, 55093, 55094)

ESTADOS_TCP = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
    "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
    "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING",
}


# ---------------------------------------------------------------- leitura ---

def ler(caminho, padrao=None):
    try:
        with open(caminho, "r", errors="replace") as f:
            return f.read()
    except OSError:
        return padrao


def le_conf(caminho=CONF):
    """k = v, como o router.conf. Ausente, valem os padroes."""
    cfg = {}
    texto = ler(caminho, "") or ""
    for linha in texto.splitlines():
        linha = linha.split("#", 1)[0].strip()
        if "=" in linha:
            k, v = linha.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def boot_id(proc):
    return (ler(os.path.join(proc, "sys/kernel/random/boot_id"), "") or "").strip()


def uptime_s(proc):
    try:
        return float((ler(os.path.join(proc, "uptime"), "") or "0").split()[0])
    except (ValueError, IndexError):
        return None


def _ip_hex(h):
    """'0B0AA8C0' (little-endian) -> '192.168.10.11'."""
    return socket.inet_ntoa(struct.pack("<I", int(h, 16)))


def parse_proc_net_tcp(texto):
    """Linhas de /proc/net/tcp -> dicts com origem, destino, estado, filas."""
    out = []
    for linha in (texto or "").splitlines()[1:]:
        p = linha.split()
        if len(p) < 7:
            continue
        try:
            lip, lport = p[1].split(":")
            rip, rport = p[2].split(":")
            txq, rxq = p[4].split(":")
            out.append({
                "local": f"{_ip_hex(lip)}:{int(lport, 16)}",
                "remoto_ip": _ip_hex(rip),
                "remoto_porta": int(rport, 16),
                "estado": ESTADOS_TCP.get(p[3].upper(), p[3]),
                "txq": int(txq, 16),
                "rxq": int(rxq, 16),
                "retr": int(p[6], 16),
            })
        except (ValueError, struct.error):
            continue
    return out


def conexoes_servidor(proc, ip, porta, ignorar_portas=PORTAS_SONDA):
    todas = parse_proc_net_tcp(ler(os.path.join(proc, "net/tcp"), ""))
    return [{k: c[k] for k in ("local", "estado", "txq", "rxq", "retr")}
            for c in todas if c["remoto_ip"] == ip and c["remoto_porta"] == porta
            and int(c["local"].rsplit(":", 1)[1]) not in ignorar_portas]


def parse_arp(texto, ip):
    """Estado da entrada ARP do ip: 'ok', 'incompleta' ou None (ausente)."""
    for linha in (texto or "").splitlines()[1:]:
        p = linha.split()
        if len(p) >= 4 and p[0] == ip:
            try:
                flags = int(p[2], 16)
            except ValueError:
                return "?"
            return "ok" if flags & 0x2 else "incompleta"
    return None


def interface(proc, sysfs, iface):
    base = os.path.join(sysfs, "class/net", iface)
    d = {"carrier": (ler(os.path.join(base, "carrier"), "") or "").strip() or None,
         "operstate": (ler(os.path.join(base, "operstate"), "") or "").strip() or None}
    for linha in (ler(os.path.join(proc, "net/dev"), "") or "").splitlines()[2:]:
        nome, _, resto = linha.partition(":")
        if nome.strip() != iface:
            continue
        c = resto.split()
        if len(c) >= 12:
            d.update(rx_bytes=int(c[0]), rx_erros=int(c[2]), rx_drop=int(c[3]),
                     tx_bytes=int(c[8]), tx_erros=int(c[10]), tx_drop=int(c[11]))
    return d


def processos_cliente(proc, padrao, eu=None):
    """Processos python cujo argumento contem `padrao`, com idade e memoria."""
    eu = os.getpid() if eu is None else eu
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    pagina = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    up = uptime_s(proc)
    out = []
    for d in glob.glob(os.path.join(proc, "[0-9]*")):
        pid = int(os.path.basename(d))
        if pid == eu:
            continue
        cmd = (ler(os.path.join(d, "cmdline"), "") or "").split("\0")
        cmd = [c for c in cmd if c]
        if not cmd or "python" not in os.path.basename(cmd[0]):
            continue
        if not any(padrao in c for c in cmd[1:]) or any("caixa_preta" in c for c in cmd):
            continue
        stat = ler(os.path.join(d, "stat"), "") or ""
        # o nome do processo vai entre parenteses e pode ter espaco: cortar no ultimo ')'
        campos = stat[stat.rfind(")") + 2:].split()
        info = {"pid": pid, "cmd": " ".join(cmd)[-120:]}
        if len(campos) > 20:
            info["estado"] = campos[0]
            info["ppid"] = int(campos[1])
            info["threads"] = int(campos[17])
            if up is not None:
                info["idade_s"] = round(up - int(campos[19]) / hz)
        statm = (ler(os.path.join(d, "statm"), "") or "").split()
        if len(statm) > 1:
            info["rss_mb"] = round(int(statm[1]) * pagina / 1048576, 1)
        out.append(info)
    return sorted(out, key=lambda x: x["pid"])


def throttled(sysfs, vcgencmd_ok=True):
    for p in glob.glob(os.path.join(sysfs, "devices/platform/soc/soc:firmware/get_throttled")):
        v = (ler(p, "") or "").strip()
        if v:
            try:
                return hex(int(v, 16))
            except ValueError:
                return v
    if not vcgencmd_ok:
        return None
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                           text=True, timeout=5)
        return r.stdout.strip().split("=")[-1] or None
    except (OSError, subprocess.SubprocessError):
        return None


def sistema(proc, sysfs):
    d = {}
    la = (ler(os.path.join(proc, "loadavg"), "") or "").split()
    if la:
        d["carga1"] = float(la[0])
    for linha in (ler(os.path.join(proc, "meminfo"), "") or "").splitlines():
        if linha.startswith("MemAvailable:"):
            d["mem_livre_mb"] = int(linha.split()[1]) // 1024
    t = (ler(os.path.join(sysfs, "class/thermal/thermal_zone0/temp"), "") or "").strip()
    if t.isdigit():
        d["temp_c"] = round(int(t) / 1000, 1)
    try:
        st = os.statvfs("/")
        d["disco_livre_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
    except OSError:
        pass
    for linha in (ler(os.path.join(proc, "mounts"), "") or "").splitlines():
        p = linha.split()
        if len(p) > 3 and p[1] == "/":
            d["raiz_ro"] = "ro" in p[3].split(",")
    d["ntp_sinc"] = os.path.exists("/run/systemd/timesync/synchronized")
    return d


# ----------------------------------------------------------------- sondas ---

def _cksum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def ping_ms(ip, timeout=2.0, _seq=[0]):
    """Um echo ICMP: ms, ou None sem resposta. Sem processo filho: socket de
    ping do kernel; se ele estiver fechado (ping_group_range), socket cru, que
    o servico pode abrir porque roda como root; por ultimo, o /bin/ping."""
    _seq[0] = (_seq[0] + 1) & 0xFFFF
    seq = _seq[0]
    ident = os.getpid() & 0xFFFF
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        cru = False
    except OSError:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            cru = True
        except OSError:
            return _ping_bin(ip, timeout)
    try:
        corpo = b"caixa-preta"
        cab = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
        cab = struct.pack("!BBHHH", 8, 0, _cksum(cab + corpo), ident, seq)
        t0 = time.monotonic()
        s.settimeout(timeout)
        s.sendto(cab + corpo, (ip, 0))
        while True:
            resto = timeout - (time.monotonic() - t0)
            if resto <= 0:
                return None
            s.settimeout(resto)
            dados, origem = s.recvfrom(2048)
            if cru:
                if origem[0] != ip:
                    continue
                dados = dados[(dados[0] & 0x0F) * 4:]       # tira o cabecalho IP
            if len(dados) < 8 or dados[0] != 0:
                continue
            _, _, _, rid, rseq = struct.unpack("!BBHHH", dados[:8])
            # no socket de ping o kernel troca o id; so o seq identifica
            if rseq == seq and (not cru or rid == ident):
                return round((time.monotonic() - t0) * 1000, 1)
    except (socket.timeout, TimeoutError):
        return None
    except OSError:
        return None
    finally:
        s.close()


def _ping_bin(ip, timeout):
    try:
        t0 = time.monotonic()
        r = subprocess.run(["ping", "-n", "-c", "1", "-W", str(max(1, int(timeout))), ip],
                           capture_output=True, timeout=timeout + 3)
        return round((time.monotonic() - t0) * 1000, 1) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def tcp(ip, porta, timeout=5.0, porta_local=None):
    """('aberta'|'recusada'|'timeout'|'erro:<errno>', ms).

    Com `porta_local`, a conexao sai dessa porta (ver PORTAS_SONDA)."""
    t0 = time.monotonic()
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if porta_local:
            s.bind(("", porta_local))
        s.settimeout(timeout)
        s.connect((ip, porta))
        r = "aberta"
    except ConnectionRefusedError:
        r = "recusada"
    except (socket.timeout, TimeoutError):
        r = "timeout"
    except OSError as e:
        r = f"erro:{e.errno}"
    finally:
        if s is not None:
            s.close()
    return r, round((time.monotonic() - t0) * 1000)


def http_data(ip, timeout=8.0):
    """(status, Date do CPE em epoch) do GET /. Nao e' hora real: as Elsys
    desta rede nao acertam o relogio. Serve para ver saltos (reinicios)."""
    c = http.client.HTTPConnection(ip, 80, timeout=timeout)
    try:
        c.request("GET", "/", headers={"Connection": "close"})
        r = c.getresponse()
        r.read(4096)
        data = r.getheader("Date")
        epoch = None
        if data:
            try:
                epoch = email.utils.parsedate_to_datetime(data).timestamp()
            except (TypeError, ValueError):
                epoch = None
        return r.status, epoch
    finally:
        c.close()


# --------------------------------------------------- arquivos do cliente ---

class Seguidor:
    """Segue um arquivo de texto como `tail -F`: devolve so as linhas novas.

    Guarda os primeiros bytes do arquivo: se eles mudam, o arquivo foi
    reescrito (truncado e regravado), mesmo que tenha crescido de novo.
    """

    CABECA = 64

    def __init__(self, caminho, ino=None, pos=None, cabeca=None):
        self.caminho, self.ino, self.pos = caminho, ino, pos
        self.cabeca = cabeca                   # hex dos primeiros bytes

    def _cabeca_atual(self):
        try:
            with open(self.caminho, "rb") as f:
                return f.read(self.CABECA).hex()
        except OSError:
            return None

    def novas(self, maximo=200):
        try:
            st = os.stat(self.caminho)
        except OSError:
            return []
        cab = self._cabeca_atual()
        if self.ino is None or self.pos is None:
            # primeira vez: comeca do fim, o historico vai no evento "historico"
            self.ino, self.pos, self.cabeca = st.st_ino, st.st_size, cab
            return []
        reescrito = self.cabeca is not None and cab is not None and \
            not cab.startswith(self.cabeca[:len(cab)]) and not self.cabeca.startswith(cab)
        if st.st_ino != self.ino or st.st_size < self.pos or reescrito:
            self.ino, self.pos = st.st_ino, 0          # rodou, truncou ou reescreveu
        self.cabeca = cab
        if st.st_size == self.pos:
            return []
        with open(self.caminho, "rb") as f:
            f.seek(self.pos)
            bruto = f.read(256 * 1024)
        fim = bruto.rfind(b"\n")
        if fim < 0:
            return []                                   # linha ainda incompleta
        self.pos += fim + 1
        linhas = bruto[:fim].decode("utf-8", "replace").splitlines()
        if len(linhas) > maximo:
            linhas = linhas[:maximo] + [f"... (+{len(linhas) - maximo} linhas)"]
        return linhas


def motivo_falha(caminho):
    for linha in (ler(caminho, "") or "").splitlines():
        if linha.startswith("Motivo:"):
            return linha[len("Motivo:"):].strip()[:300]
    return None


class VigiaFalhas:
    """Arquivos novos em logs_failure. So relista quando o mtime da pasta muda:
    a 106 chegou a ter dezenas de milhares de arquivos ali.

    Entre execucoes guarda so o maior nome visto: os nomes sao
    falha_AAAA-MM-DD_HH-MM-SS.txt e ordenam no tempo da Pi.
    """

    def __init__(self, pasta, mtime=None, ultimo=None):
        self.pasta = pasta
        self.mtime = mtime
        self.ultimo = ultimo
        self.vistos = None

    def novos(self):
        try:
            m = os.stat(self.pasta).st_mtime
        except OSError:
            return []
        if self.vistos is not None and m == self.mtime:
            return []
        try:
            nomes = set(os.listdir(self.pasta))
        except OSError:
            return []
        self.mtime = m
        if self.vistos is None:
            novos = sorted(n for n in nomes if self.ultimo and n > self.ultimo)
        else:
            novos = sorted(nomes - self.vistos)
        self.vistos = nomes
        if nomes:
            self.ultimo = max(nomes)
        return [(n, motivo_falha(os.path.join(self.pasta, n))) for n in novos]


def historico(cfg, maximo_linhas=200, maximo_falhas=50):
    """Foto do que ja existia na primeira vez que o agente roda."""
    log = os.path.join(cfg["base"], "router.log")
    linhas = (ler(log, "") or "").splitlines()
    falhas = []
    try:
        nomes = sorted(os.listdir(cfg["logs_failure"]))
    except OSError:
        nomes = []
    for n in nomes[-maximo_falhas:]:
        p = os.path.join(cfg["logs_failure"], n)
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            mt = None
        falhas.append({"arquivo": n, "mtime": mt, "motivo": motivo_falha(p)})
    return {"router_log_total_linhas": len(linhas),
            "router_log_fim": linhas[-maximo_linhas:],
            "logs_failure_total": len(nomes),
            "logs_failure_recentes": falhas}


# --------------------------------------------------------------- registro ---

class Registro:
    """JSONL com fsync por linha e rotacao por tamanho."""

    def __init__(self, pasta, rotacao_mb=20, manter=100, nome="atual.jsonl", prefixo="cp-"):
        self.pasta = pasta
        self.limite = int(rotacao_mb) * 1024 * 1024
        self.manter = int(manter)
        self.prefixo = prefixo
        os.makedirs(pasta, exist_ok=True)
        self.atual = os.path.join(pasta, nome)

    def grava(self, reg):
        linha = json.dumps(reg, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.atual, "a", encoding="utf-8") as f:
            f.write(linha)
            f.flush()
            os.fsync(f.fileno())
        if os.path.getsize(self.atual) > self.limite:
            self._roda()

    def _roda(self):
        nome = os.path.join(self.pasta,
                            time.strftime(self.prefixo + "%Y%m%d-%H%M%S.jsonl"))
        while os.path.exists(nome):
            nome = nome.replace(".jsonl", "_.jsonl")
        os.replace(self.atual, nome)
        antigos = sorted(glob.glob(os.path.join(self.pasta, self.prefixo + "*.jsonl")),
                         key=os.path.getmtime)
        for p in antigos[:-self.manter] if self.manter else []:
            try:
                os.unlink(p)
            except OSError:
                pass


# ------------------------------------------------------------- radio Elsys ---

# Os valores vem escritos no HTML de /status.html (o JS so os copia para a
# tela): window.nivel_sinal=_(null,'-64 dBm'), window.levelQuality='-5 dB'. O
# index.html?status que o navegador abre e' so a moldura: ele carrega o
# status.html por jQuery - lido direto, o index nao traz os valores (102, 17/10).
# So o que serve ao enlace e' extraido - a mesma pagina traz IMEI e ICCID, que
# nunca vao para o registro.
RX_RADIO = {
    "rsrp": re.compile(r"nivel_sinal=_\(null,'([^']*)'\)"),
    "rsrq": re.compile(r"levelQuality='([^']*)'"),
    "pct": re.compile(r"nivel_sinal_percent=_\(null,'([^']*)'\)"),
    "con": re.compile(r"ip_protocol=_\(null,'([^']*)'\)"),
    "rede": re.compile(r"tipo_rede_movel='([^']*)'"),
}
RADIO_QUEDA_DB = 10          # RSRP esta' abaixo da linha de base: radio_caiu
RADIO_VOLTA_DB = 5           # e voltou para perto dela: radio_voltou
RADIO_BASE_N = 12            # linha de base: mediana das ultimas 12 leituras boas
RADIO_RECUO_MAX_S = 300      # leitura falhando: espaca ate' 5 min
# Login recusado: so tenta de novo depois de 1 h. Se a Elsys bloquear a conta
# depois de varias tentativas erradas, quem perde e' o watchdog, que usa o
# mesmo login para reiniciar a Elsys na hora da queda.
RADIO_RECUO_LOGIN_S = 3600


class LoginRecusado(RuntimeError):
    pass


def _numero(txt):
    m = re.search(r"-?\d+(?:\.\d+)?", txt or "")
    return float(m.group()) if m else None


def pagina_de_login(html):
    return "loginForm" in html or "<title>Elsys - Login" in html


def parse_status_elsys(html):
    """RSRP, RSRQ, % de sinal e estado da conexao; None se nao for a pagina."""
    if pagina_de_login(html):
        return None
    bruto = {}
    for k, rx in RX_RADIO.items():
        m = rx.search(html)
        bruto[k] = m.group(1) if m else None
    if bruto["rsrp"] is None and bruto["con"] is None:
        return None
    return {"rsrp": _numero(bruto["rsrp"]),
            "rsrq": _numero(bruto["rsrq"]),
            "pct": _numero(bruto["pct"]),
            "con": (bruto["con"] or "").split("|")[-1] or None,
            "rede": bruto["rede"]}


def le_router_conf(caminho):
    """usuario/senha do router.conf do watchdog (k = v, # comenta)."""
    cfg = {}
    try:
        with open(caminho) as f:
            for linha in f:
                linha = linha.split("#", 1)[0].strip()
                if "=" in linha:
                    k, v = linha.split("=", 1)
                    cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


def _sha256(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class LeitorElsys:
    """Le a pagina de status da Elsys. Firmware novo: login por formulario
    (/boafrm/loginForm com sha256 do usuario e da senha, como o JS da pagina
    faz), sessao em cookie, refeito quando a pagina volta a ser a de login.
    Firmware antigo: a pagina abre direto, ou com Basic auth."""

    def __init__(self, url, router_conf, timeout=4.0):
        self.url = url
        partes = urllib.parse.urlsplit(url)
        self.raiz = f"{partes.scheme}://{partes.netloc}"
        self.router_conf = router_conf
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.logins = 0
        self._basic = None

    def _cred(self):
        c = le_router_conf(self.router_conf)
        return c.get("usuario") or "admin", c.get("senha") or ""

    def _get(self):
        req = urllib.request.Request(self.url)
        if self._basic:
            req.add_header("Authorization", self._basic)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.read(300000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 401 or self._basic:
                raise
            usuario, senha = self._cred()
            self._basic = "Basic " + base64.b64encode(
                f"{usuario}:{senha}".encode()).decode()
            return self._get()

    def _login(self):
        usuario, senha = self._cred()
        if not senha:
            raise LoginRecusado(f"a Elsys pede login e {self.router_conf} nao tem 'senha'")
        dados = urllib.parse.urlencode({"username": "", "password": "",
                                        "user_hash": _sha256(usuario),
                                        "pass_hash": _sha256(senha)}).encode()
        req = urllib.request.Request(self.raiz + "/boafrm/loginForm", data=dados,
                                     method="POST")
        with self.opener.open(req, timeout=self.timeout) as r:
            r.read(300000)
        self.logins += 1

    def le(self):
        html = self._get()
        if pagina_de_login(html):
            self._login()
            html = self._get()
            if pagina_de_login(html):
                raise LoginRecusado("login recusado pela Elsys")
        r = parse_status_elsys(html)
        if r is None:
            raise RuntimeError("pagina de status nao reconhecida")
        return r


def _arquivo_fresco(caminho, validade_s):
    try:
        return time.time() - os.path.getmtime(caminho) < validade_s
    except OSError:
        return False


def carrega_estado(caminho):
    try:
        with open(caminho) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def salva_estado(caminho, estado):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w") as f:
        json.dump(estado, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, caminho)


# ------------------------------------------------------------------ agente ---

class Agente:
    def __init__(self, cfg, registro=None, agora=time.time, mono=time.monotonic):
        self.cfg = cfg
        self.agora, self.mono = agora, mono
        self.reg = registro or Registro(cfg["saida"], cfg["rotacao_mb"], cfg["manter_arquivos"])
        self.estado = carrega_estado(cfg["estado"])
        rl = self.estado.get("router_log", {})
        self.router = Seguidor(os.path.join(cfg["base"], "router.log"),
                               rl.get("ino"), rl.get("pos"), rl.get("cabeca"))
        lf = self.estado.get("logs_failure", {})
        self.falhas = VigiaFalhas(cfg["logs_failure"], lf.get("mtime"), lf.get("ultimo"))
        self.boot = boot_id(cfg["proc"])
        self.ant = {}
        self.ultimo = {"cpe_tcp": -1e9, "cpe_http": -1e9, "servidor": -1e9, "vcgencmd": -1e9}
        self.throttled_cache = None
        self.fila_presa = 0
        self.fila_avisada = False
        self._porta = 0
        self.radio_s = float(cfg.get("radio_s") or 0)
        self.leitor = None
        self.reg_radio = None
        if self.radio_s > 0:
            url = cfg.get("radio_url") or f"http://{cfg['gateway']}/status.html"
            conf = cfg.get("router_conf") or os.path.join(cfg["base"], "router.conf")
            self.leitor = LeitorElsys(url, conf)
            self.reg_radio = Registro(cfg["saida"], cfg["rotacao_mb"], cfg["manter_arquivos"],
                                      nome="radio.jsonl", prefixo="radio-")
        self.radio_ok = None
        self.radio_falhas = 0
        self.radio_login_recusado = False
        self.radio_base = []
        self.radio_baixo = None          # linha de base no momento do radio_caiu

    # -- registro
    def _base(self, tipo):
        return {"tipo": tipo, "t": round(self.agora(), 1), "mono": round(self.mono(), 1),
                "boot": self.boot[:8], "cam": self.cfg["camera"]}

    def evento(self, nome, **dados):
        r = self._base("evento")
        r["ev"] = nome
        r.update(dados)
        self.reg.grava(r)
        # tambem no journal (persistente): redundancia barata
        print(json.dumps(r, ensure_ascii=False), flush=True)

    # -- partida
    def inicio(self):
        cfg = self.cfg
        self.evento("inicio", versao=VERSAO, pid=os.getpid(),
                    cfg={k: cfg[k] for k in ("gateway", "wan", "servidor", "porta", "intervalo")})
        ultimo_boot = self.estado.get("boot")
        if ultimo_boot and ultimo_boot != self.boot:
            self.evento("boot_novo", boot_anterior=ultimo_boot[:8],
                        ultima_amostra_t=self.estado.get("ultima_t"),
                        uptime_s=uptime_s(cfg["proc"]))
        if not self.estado.get("historico_feito"):
            self.evento("historico", **historico(cfg))
            self.estado["historico_feito"] = True
        # o que entrou nos arquivos enquanto o agente esteve fora (na primeira
        # vez os seguidores so se posicionam no fim: o passado foi no "historico")
        self._arquivos_do_cliente()

    # -- um ciclo
    def ciclo(self):
        cfg, m = self.cfg, self.mono()
        a = self._base("amostra")
        a["up"] = uptime_s(cfg["proc"])
        a["gw_ms"] = ping_ms(cfg["gateway"])
        a["gw_arp"] = parse_arp(ler(os.path.join(cfg["proc"], "net/arp"), ""), cfg["gateway"])
        a["wan_ms"] = ping_ms(cfg["wan"]) if cfg["wan"] else None
        a["eth"] = interface(cfg["proc"], cfg["sys"], cfg["iface"])
        a["conn"] = conexoes_servidor(cfg["proc"], cfg["servidor"], int(cfg["porta"]))
        a["cliente"] = processos_cliente(cfg["proc"], cfg["padrao_cliente"])
        a["sis"] = sistema(cfg["proc"], cfg["sys"])

        usa_vcgencmd = m - self.ultimo["vcgencmd"] >= CADA_VCGENCMD
        thr = throttled(cfg["sys"], vcgencmd_ok=usa_vcgencmd)
        if thr is None and not usa_vcgencmd:
            thr = self.throttled_cache
        elif usa_vcgencmd:
            self.ultimo["vcgencmd"] = m
        self.throttled_cache = thr
        a["sis"]["throttled"] = thr

        if m - self.ultimo["cpe_tcp"] >= CADA_CPE_TCP:
            self.ultimo["cpe_tcp"] = m
            a["cpe80"] = tcp(cfg["gateway"], 80)
        conectado = any(c["estado"] == "ESTABLISHED" for c in a["conn"])
        if not conectado and m - self.ultimo["servidor"] >= CADA_SERVIDOR:
            # Sonda o servidor so com o cliente desconectado: o servidor descarta
            # conexao sem ID sem gravar linha, mas nao ha por que somar trafego.
            self.ultimo["servidor"] = m
            a["servidor"] = tcp(cfg["servidor"], int(cfg["porta"]),
                                porta_local=self._proxima_porta())

        self.reg.grava(a)
        self._transicoes(a, conectado)

        if m - self.ultimo["cpe_http"] >= CADA_CPE_HTTP:
            self.ultimo["cpe_http"] = m
            try:
                st, data = http_data(cfg["gateway"])
                self.evento("relogio", http_status=st, cpe_date=data,
                            offset_s=round(data - self.agora(), 1) if data else None)
            except Exception as e:                       # noqa: BLE001 - so registra
                self.evento("relogio", erro=repr(e)[:200])

        self._arquivos_do_cliente()

        self.estado.update(boot=self.boot, ultima_t=a["t"],
                           router_log={"ino": self.router.ino, "pos": self.router.pos,
                                       "cabeca": self.router.cabeca},
                           logs_failure={"mtime": self.falhas.mtime,
                                         "ultimo": self.falhas.ultimo})
        salva_estado(cfg["estado"], self.estado)
        return a

    # -- radio da Elsys
    def passo_radio(self):
        if self.radio_login_recusado:
            return RADIO_RECUO_LOGIN_S
        if self.radio_falhas == 0:
            return self.radio_s
        return min(RADIO_RECUO_MAX_S, self.radio_s * 2 ** min(self.radio_falhas, 8))

    def radio(self, leitor=None):
        leitor = leitor or self.leitor
        if leitor is None:
            return None
        # O watchdog esta' logado na Elsys reiniciando-a: nao disputar a sessao.
        if _arquivo_fresco(os.path.join(self.cfg["base"], "router.lock"), 300):
            return None
        r = {"t": round(self.agora(), 1), "mono": round(self.mono(), 1)}
        recusado = False
        try:
            r.update(leitor.le())
            ok = True
        except Exception as e:                               # noqa: BLE001
            r["erro"] = f"{type(e).__name__}: {e}"[:160]
            ok = False
            recusado = isinstance(e, LoginRecusado)
        self.radio_login_recusado = recusado
        self.reg_radio.grava(r)
        self._transicoes_radio(r, ok)
        return r

    def _transicoes_radio(self, r, ok):
        if ok != self.radio_ok:
            if not ok:
                self.evento("radio_sem_leitura", erro=r.get("erro"))
            elif self.radio_ok is None:
                self.evento("radio_inicial", rsrp=r.get("rsrp"), rsrq=r.get("rsrq"),
                            con=r.get("con"))
            else:
                self.evento("radio_leitura_voltou", rsrp=r.get("rsrp"), con=r.get("con"))
            self.radio_ok = ok
        self.radio_falhas = 0 if ok else self.radio_falhas + 1
        if not ok:
            return
        rsrp = r.get("rsrp")
        base = sorted(self.radio_base)[len(self.radio_base) // 2] if self.radio_base else None
        if self.radio_baixo is None:
            if base is not None and (rsrp is None or base - rsrp >= RADIO_QUEDA_DB):
                self.radio_baixo = base
                self.evento("radio_caiu", rsrp=rsrp, base=base, con=r.get("con"))
            elif rsrp is not None:
                self.radio_base = (self.radio_base + [rsrp])[-RADIO_BASE_N:]
        elif rsrp is not None and self.radio_baixo - rsrp < RADIO_VOLTA_DB:
            self.evento("radio_voltou", rsrp=rsrp, base=self.radio_baixo)
            self.radio_baixo = None
            self.radio_base = (self.radio_base + [rsrp])[-RADIO_BASE_N:]

    def _proxima_porta(self):
        self._porta = (self._porta + 1) % len(PORTAS_CAIXA_PRETA)
        return PORTAS_CAIXA_PRETA[self._porta]

    def _arquivos_do_cliente(self):
        for linha in self.router.novas():
            self.evento("router_log", linha=linha[:500])
        for nome, motivo in self.falhas.novos():
            self.evento("logs_failure", arquivo=nome, motivo=motivo)

    def _transicoes(self, a, conectado):
        ant = self.ant
        if ant:
            def muda(chave, novo, velho, ev_sim, ev_nao, **extra):
                if bool(novo) != bool(velho):
                    self.evento(ev_sim if novo else ev_nao, **extra)

            muda("gw", a["gw_ms"] is not None, ant["gw_ms"] is not None,
                 "gateway_voltou", "gateway_perdido", arp=a["gw_arp"])
            if a["gw_arp"] != ant["gw_arp"]:
                self.evento("arp_gateway", de=ant["gw_arp"], para=a["gw_arp"])
            if self.cfg["wan"]:
                muda("wan", a["wan_ms"] is not None, ant["wan_ms"] is not None,
                     "wan_voltou", "wan_perdida")
            if a["eth"].get("carrier") != ant["eth"].get("carrier"):
                self.evento("cabo", de=ant["eth"].get("carrier"), para=a["eth"].get("carrier"))
            if "cpe80" in a and "cpe80" in ant and a["cpe80"][0] != ant["cpe80"][0]:
                self.evento("cpe_web", de=ant["cpe80"][0], para=a["cpe80"][0])
            ec = sorted(c["estado"] for c in a["conn"])
            ea = sorted(c["estado"] for c in ant["conn"])
            if ec != ea:
                self.evento("conexao_servidor", de=ea, para=ec, conn=a["conn"])
            pa = {c["pid"] for c in ant["cliente"]}
            pn = {c["pid"] for c in a["cliente"]}
            for c in a["cliente"]:
                if c["pid"] not in pa:
                    self.evento("cliente_novo", **c)
            for pid in sorted(pa - pn):
                self.evento("cliente_sumiu", pid=pid)
            if a["sis"].get("throttled") != ant["sis"].get("throttled") and a["sis"].get("throttled"):
                self.evento("throttled", de=ant["sis"].get("throttled"), para=a["sis"]["throttled"])
            if a["sis"].get("raiz_ro") and not ant["sis"].get("raiz_ro"):
                self.evento("raiz_somente_leitura")
            if a["sis"].get("ntp_sinc") != ant["sis"].get("ntp_sinc"):
                self.evento("ntp", sincronizado=a["sis"].get("ntp_sinc"))
            if "servidor" in a:
                if a["servidor"][0] != (ant.get("ult_servidor") or [None])[0]:
                    self.evento("sonda_servidor", resultado=a["servidor"])
        else:
            # primeira amostra desta execucao: o ponto de partida da linha do tempo
            self.evento("estado_inicial", gw_ms=a["gw_ms"], gw_arp=a["gw_arp"],
                        wan_ms=a["wan_ms"], cabo=a["eth"].get("carrier"),
                        conn=a["conn"], cliente=a["cliente"],
                        throttled=a["sis"].get("throttled"))
        if "servidor" in a:
            a["ult_servidor"] = a["servidor"]
        elif ant.get("ult_servidor"):
            a["ult_servidor"] = ant["ult_servidor"]
        if "cpe80" not in a and "cpe80" in ant:
            a["cpe80"] = ant["cpe80"]

        # Fila presa: conectado, com bytes na fila de envio e o contador de
        # bytes da placa parado. E' a assinatura de "o caminho morreu com a
        # conexao ainda aberta" - o que antecede um Timeout no servidor.
        presa = conectado and any(c["txq"] > 0 for c in a["conn"]) and \
            ant and a["eth"].get("tx_bytes") is not None and \
            a["eth"].get("tx_bytes", 0) - ant["eth"].get("tx_bytes", 0) < 2000
        self.fila_presa = self.fila_presa + 1 if presa else 0
        if self.fila_presa >= CICLOS_FILA_PRESA and not self.fila_avisada:
            self.evento("fila_presa", ciclos=self.fila_presa, conn=a["conn"])
            self.fila_avisada = True
        elif not presa and self.fila_avisada:
            self.evento("fila_destravou")
            self.fila_avisada = False
        self.ant = a

    def _protegido(self, f):
        try:
            f()
        except Exception:                                    # noqa: BLE001
            try:
                self.evento("erro", tb=traceback.format_exc()[-1500:])
            except Exception:                                # noqa: BLE001
                pass

    def laco(self):
        self.inicio()
        intervalo = float(self.cfg["intervalo"])
        prox = prox_radio = self.mono()
        while True:
            if self.mono() >= prox:
                self._protegido(self.ciclo)
                prox += intervalo
                if prox < self.mono():
                    prox = self.mono()
            if self.leitor is not None and self.mono() >= prox_radio:
                self._protegido(self.radio)
                prox_radio = self.mono() + self.passo_radio()
            alvo = min(prox, prox_radio) if self.leitor is not None else prox
            time.sleep(max(0.0, alvo - self.mono()))


# --------------------------------------------------------------- relatorio ---

def le_registros(pasta, prefixo="cp-", nome="atual.jsonl"):
    arquivos = sorted(glob.glob(os.path.join(pasta, prefixo + "*.jsonl")), key=os.path.getmtime)
    arquivos.append(os.path.join(pasta, nome))
    for p in arquivos:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for linha in f:
                    try:
                        yield json.loads(linha)
                    except ValueError:
                        continue
        except OSError:
            continue


def parse_offset(texto):
    """Offset em segundos a partir de "3460000", "-90", "40d1h4m", "40d 01h04m05s"
    ou de um par "hora da Pi=hora real" ("2026-09-04 21:29:58=2026-10-14 22:34:03",
    as duas no fuso da Pi). Levanta ValueError se nao entender."""
    texto = texto.strip()
    if "=" in texto:
        pi, real = (time.mktime(time.strptime(x.strip(), "%Y-%m-%d %H:%M:%S"))
                    for x in texto.split("=", 1))
        return real - pi
    try:
        return float(texto)
    except ValueError:
        pass
    m = re.fullmatch(r"([+-])?\s*(?:(\d+(?:\.\d+)?)\s*d)?\s*(?:(\d+(?:\.\d+)?)\s*h)?"
                     r"\s*(?:(\d+(?:\.\d+)?)\s*m(?:in)?)?\s*(?:(\d+(?:\.\d+)?)\s*s)?",
                     texto)
    if not m or not any(m.groups()[1:]):
        raise ValueError(f"offset nao reconhecido: {texto!r}")
    d, h, mi, s = (float(x or 0) for x in m.groups()[1:])
    total = d * 86400 + h * 3600 + mi * 60 + s
    return -total if m.group(1) == "-" else total


def relatorio(pasta, horas=None, intervalo=30, saida=None, offset=None):
    """Linha do tempo: eventos, lacunas entre amostras e reboots, com hora da
    Pi e hora real estimada.

    `offset` (segundos, hora real - hora da Pi) vem de fora, calibrado pelo
    LOG_connections do PC, e vale para todas as linhas. Sem ele, a coluna da
    hora real fica em branco: o relogio da Elsys nunca e' usado."""
    saida = saida or sys.stdout
    regs = list(le_registros(pasta))
    fixo = offset is not None
    cpe = [r for r in regs if r.get("ev") == "relogio" and r.get("cpe_date")]
    if horas:
        corte = max((r.get("t", 0) for r in regs), default=0) - horas * 3600
        regs = [r for r in regs if r.get("t", 0) >= corte]

    def hora(t):
        return time.strftime("%d/%m %H:%M:%S", time.localtime(t))

    def real(t):
        return "      ?       " if offset is None else hora(t + offset)

    if fixo:
        print(f"# {len(regs)} registros. Offset informado: {offset:+.0f} s = "
              f"{offset / 86400:+.2f} dias", file=saida)
    else:
        print(f"# {len(regs)} registros. Sem hora real: o relogio da Elsys nao serve "
              f"de referencia nesta rede", file=saida)
        if cpe:
            diz = time.strftime("%d/%m/%Y", time.localtime(cpe[-1]["cpe_date"]))
            print(f"# (agora ela diz {diz} - sem internet, nunca acerta)", file=saida)
        print("# Calibre pelo PC: a hora da Pi de uma conexao aberta (evento "
              "conexao_servidor) contra o CONNECT\n# no LOG_connections, e rode de "
              "novo com --offset \"AAAA-MM-DD HH:MM:SS=AAAA-MM-DD HH:MM:SS\" "
              "(Pi=real).", file=saida)
    print("# hora da Pi        | hora real estimada | evento", file=saida)
    ultima = None
    for r in regs:
        t = r.get("t", 0)
        if r.get("tipo") == "amostra":
            if ultima and ultima.get("boot") == r.get("boot") and \
                    r.get("mono", 0) - ultima.get("mono", 0) > 3 * intervalo:
                buraco = r["mono"] - ultima["mono"]
                print(f"{hora(t)} | {real(t)} | LACUNA de {buraco:.0f} s sem "
                      f"amostra (agente ou sistema parado)", file=saida)
            ultima = r
            continue
        dados = {k: v for k, v in r.items()
                 if k not in ("tipo", "t", "mono", "boot", "cam", "ev")}
        texto = json.dumps(dados, ensure_ascii=False)
        if len(texto) > 400:
            texto = texto[:400] + "..."
        print(f"{hora(t)} | {real(t)} | {r.get('ev')} {texto}", file=saida)
    if ultima:
        print(f"# ultima amostra: {hora(ultima['t'])} (Pi) / {real(ultima['t']).strip()} "
              f"(real). gw={ultima.get('gw_ms')} conn="
              f"{[c['estado'] for c in ultima.get('conn', [])]} "
              f"cliente={[c['pid'] for c in ultima.get('cliente', [])]}", file=saida)


# ------------------------------------------------------ relatorio simples ---
# --simples: a mesma linha do tempo em frases, para quem nao conhece os nomes
# dos eventos. Esconde o que e' ruido para leitura (stacktrace do Selenium, o
# relogio da Elsys, o arp que repete o gateway) e fecha com um resumo.

_ROUTER_RUIDO = ("Stacktrace:", "  (Session info", "#")


def _sem_hora(linha):
    """'2026-09-09 08:28:47 | texto' -> 'texto' (o watchdog do repositorio)."""
    if len(linha) > 22 and linha[4] == "-" and linha[19:22] == " | ":
        return linha[22:]
    return linha


def _frase_router(linha):
    linha = _sem_hora((linha or "").rstrip())
    if not linha.strip() or linha.startswith(_ROUTER_RUIDO):
        return None
    baixo = linha.lower()
    if "reinicializando roteador" in baixo:
        return "Watchdog: vai reiniciar a Elsys"
    if "reiniciado com sucesso" in baixo:
        return "Watchdog: diz que reiniciou a Elsys"
    if baixo.startswith("erro ao reiniciar"):
        motivo = linha.split("Message:", 1)[-1].strip()
        return f"Watchdog: FALHOU ao reiniciar a Elsys ({motivo[:90]})"
    if baixo.startswith("pulando"):
        return "Watchdog: nao reiniciou - a Elsys foi reiniciada ha menos de 10 min"
    if "pagina da falha salva" in baixo:
        return "Watchdog: guardou a tela da falha (router_erro.png)"
    if "reboot clicado" in baixo:
        return "Watchdog: clicou em Reiniciar na pagina da Elsys"
    if "firmware novo" in baixo:
        return "Watchdog: entrou na pagina da Elsys (firmware novo, com login)"
    if "firmware antigo" in baixo:
        return "Watchdog: entrou na pagina da Elsys (sem login)"
    if "botao de reboot achado" in baixo:
        return None
    if baixo.startswith("--seco"):
        return "Watchdog (teste --seco): login e botao ok, nada foi clicado"
    return f"Watchdog: {linha[:120]}"


def _tem(lista, estado):
    return estado in (lista or [])


def _frase_conexao(r):
    de, para = r.get("de") or [], r.get("para") or []
    if _tem(para, "ESTABLISHED") and not _tem(de, "ESTABLISHED"):
        return "Camera CONECTOU ao servidor (voltou a transmitir)"
    if _tem(de, "ESTABLISHED") and not _tem(para, "ESTABLISHED"):
        presos = sum(c.get("txq", 0) for c in r.get("conn", []))
        extra = f"; {presos / 1024:.0f} KB de frames presos sem sair" if presos > 1024 else ""
        return f"A conexao da camera com o servidor CAIU{extra}"
    if _tem(para, "SYN_SENT") and not _tem(para, "ESTABLISHED"):
        return "O cliente esta tentando conectar ao servidor (sem resposta ainda)"
    if not para:
        return "Nenhuma conexao com o servidor"
    if _tem(para, "ESTABLISHED"):
        return None          # segue conectada; so' uma conexao velha fechando
    return f"Conexao com o servidor: {'/'.join(de) or '-'} -> {'/'.join(para)}"


def _frase_teste(res):
    res = res[0] if isinstance(res, (list, tuple)) else res
    res = str(res)
    if res == "aberta" or res == "cliente_conectado":
        return "ok"
    if res.startswith("erro:101"):
        return "rede inalcancavel (a Elsys esta sem LTE)"
    if res.startswith("erro:111") or res == "recusada":
        return "recusada"
    if "timeout" in res or res.startswith("erro:110"):
        return "sem resposta"
    return res


def _fmt_dur(s):
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def frase_evento(r):
    """Uma frase em portugues simples para o evento, ou None para esconder."""
    ev, d = r.get("ev"), r
    if ev in ("relogio", "arp_gateway", "historico"):
        return None
    if ev == "router_log":
        return _frase_router(d.get("linha"))
    if ev == "conexao_servidor":
        return _frase_conexao(d)
    if ev == "cabo":
        return ("O cabo de rede da Pi voltou a ter sinal" if d.get("para") == "1"
                else "O cabo de rede da Pi PERDEU o sinal (cabo solto, Elsys sem energia "
                     "ou o auto-reparo derrubando o link)")
    if ev == "reparo":
        acao = {"renegociar_endereco": "pediu de novo o endereco de rede da Pi",
                "derrubar_link": "desligou e religou a placa de rede da Pi por 15 s"
                }.get(d.get("acao"), d.get("acao"))
        return (f"Auto-reparo {acao}, depois de {_fmt_dur(d.get('fora_s', 0))} sem caminho "
                f"(nao reinicia a Elsys)")
    if ev == "reparo_pulado":
        return "Auto-reparo nao agiu: atingiu o limite de acoes do dia"
    if ev == "reparo_teria_agido":
        return f"(modo observar) o auto-reparo teria feito: {d.get('acao')}"
    if ev == "caminho_ruim":
        return f"Auto-reparo: caminho ate o servidor fora ({_frase_teste(d.get('teste'))})"
    if ev == "caminho_ok":
        por = d.get("resolvido_por")
        quem = f", resolvido pelo auto-reparo ({por})" if por else ""
        return f"Auto-reparo: caminho ate o servidor de volta, apos {_fmt_dur(d.get('fora_s', 0))}{quem}"
    if ev == "sonda_servidor":
        return f"Teste de conexao ao servidor: {_frase_teste(d.get('resultado'))}"
    if ev == "cpe_web":
        return ("A pagina da Elsys voltou a responder" if d.get("para") == "aberta"
                else f"A pagina da Elsys nao responde ({_frase_teste(d.get('para'))})")
    if ev == "boot_novo":
        ult = d.get("ultima_amostra_t")
        quando = (" Ultimo registro antes de desligar: "
                  + time.strftime("%d/%m %H:%M:%S", time.localtime(ult))) if ult else ""
        return "A Pi LIGOU (foi reiniciada ou a energia voltou)." + quando
    if ev == "inicio":
        return f"Caixa-preta iniciou (versao {d.get('versao')})"
    if ev == "auto_reparo_inicio":
        return f"Auto-reparo iniciou (modo {d.get('modo')})"
    if ev == "estado_inicial":
        gw = "responde" if d.get("gw_ms") is not None else "NAO responde"
        conn = [c.get("estado") for c in d.get("conn", [])]
        cli = "rodando" if d.get("cliente") else "PARADO"
        return (f"Estado ao iniciar: Elsys {gw} na rede local; camera "
                f"{'conectada' if 'ESTABLISHED' in conn else 'NAO conectada'} ao servidor; "
                f"cliente {cli}")
    if ev == "cliente_novo":
        return "O programa da camera (cliente) iniciou"
    if ev == "cliente_sumiu":
        return "O programa da camera (cliente) PAROU"
    if ev == "logs_failure":
        return f"O cliente da camera registrou uma falha: {d.get('motivo')}"
    if ev == "fila_presa":
        return "Os frames pararam de sair com a conexao aberta (o enlace morreu sem avisar)"
    if ev == "fila_destravou":
        return "Os frames voltaram a sair"
    if ev == "throttled":
        v = d.get("para") or d.get("valor")
        return ("A Pi saiu da subtensao/superaquecimento" if v == "0x0"
                else f"A Pi esta com SUBTENSAO ou SUPERAQUECIMENTO ({v})")
    if ev == "radio_inicial":
        return f"Primeira leitura do sinal: RSRP {d.get('rsrp')} dBm"
    if ev == "radio_caiu":
        return f"O SINAL CAIU: RSRP {d.get('rsrp')} dBm (normal: {d.get('base')})"
    if ev == "radio_voltou":
        return f"O sinal voltou ao normal: RSRP {d.get('rsrp')} dBm"
    if ev == "radio_sem_leitura":
        return f"Nao conseguiu ler o sinal na pagina da Elsys ({d.get('erro')})"
    if ev == "radio_leitura_voltou":
        return f"Voltou a ler o sinal: RSRP {d.get('rsrp')} dBm"
    if ev == "vnc_reiniciado":
        return "Auto-reparo reiniciou o VNC"
    if ev == "pausado":
        return "Auto-reparo PAUSADO (manutencao)"
    if ev == "retomado":
        return "Auto-reparo retomado"
    simples = {
        "wan_perdida": "A Elsys PERDEU O LTE (a camera ficou sem saida para o servidor)",
        "wan_voltou": "O LTE da Elsys VOLTOU",
        "gateway_perdido": "A Elsys SUMIU da rede local (reiniciando, sem energia ou cabo)",
        "gateway_voltou": "A Elsys voltou a responder na rede local",
    }
    if ev in simples:
        return simples[ev]
    dados = {k: v for k, v in d.items() if k not in ("tipo", "t", "mono", "boot", "cam", "ev")}
    return f"{ev} {json.dumps(dados, ensure_ascii=False)[:160]}"


def relatorio_simples(pasta, horas=None, intervalo=30, saida=None, offset=None):
    """Linha do tempo em frases, com um resumo no fim."""
    saida = saida or sys.stdout
    regs = list(le_registros(pasta))
    if horas:
        corte = max((r.get("t", 0) for r in regs), default=0) - horas * 3600
        regs = [r for r in regs if r.get("t", 0) >= corte]

    def hora(t):
        return time.strftime("%d/%m %H:%M:%S", time.localtime(t + (offset or 0)))

    qual = "hora real" if offset is not None else "hora da Pi (atrasada; use --offset)"
    print(f"# {qual}  |  o que aconteceu", file=saida)
    ultima = None
    anterior = None
    lte_fora = None
    quedas, reinicios, falhas, boots = [], 0, 0, 0
    for r in regs:
        t = r.get("t", 0)
        if r.get("tipo") == "amostra":
            if ultima and ultima.get("boot") == r.get("boot") and \
                    r.get("mono", 0) - ultima.get("mono", 0) > 3 * intervalo:
                buraco = r["mono"] - ultima["mono"]
                print(f"{hora(t)}  |  A caixa-preta ficou {_fmt_dur(buraco)} sem registrar "
                      f"(Pi travada, desligada ou caixa-preta parada)", file=saida)
            ultima = r
            continue
        ev = r.get("ev")
        if ev == "wan_perdida":
            lte_fora = t
        elif ev == "boot_novo":
            boots += 1
            lte_fora = None
        frase = frase_evento(r)
        if ev == "wan_voltou" and lte_fora is not None:
            quedas.append(t - lte_fora)
            frase += f" (ficou fora {_fmt_dur(t - lte_fora)})"
            lte_fora = None
        if frase and frase.startswith("Watchdog: vai reiniciar"):
            reinicios += 1
        if frase and frase.startswith("Watchdog: FALHOU"):
            falhas += 1
        if not frase or frase == anterior:
            continue
        anterior = frase
        print(f"{hora(t)}  |  {frase}", file=saida)

    print("#", file=saida)
    print("# RESUMO", file=saida)
    if quedas:
        print(f"#  quedas de LTE que voltaram: {len(quedas)} (mais curta "
              f"{_fmt_dur(min(quedas))}, mais longa {_fmt_dur(max(quedas))})", file=saida)
    else:
        print("#  nenhuma queda de LTE que tenha voltado no periodo", file=saida)
    if lte_fora is not None:
        print(f"#  ATENCAO: o LTE caiu em {hora(lte_fora)} e nao voltou ate o fim do registro",
              file=saida)
    print(f"#  reinicios da Elsys pelo watchdog: {reinicios} tentados, {falhas} falharam",
          file=saida)
    if boots:
        print(f"#  a Pi ligou {boots} vez(es) no periodo (reinicio ou falta de energia)",
              file=saida)
    if ultima:
        conn = [c["estado"] for c in ultima.get("conn", [])]
        print(f"#  ultimo registro {hora(ultima['t'])}: camera "
              f"{'CONECTADA' if 'ESTABLISHED' in conn else 'NAO conectada'} ao servidor",
              file=saida)


def _sumiu(r):
    """Leitura que mostra o LTE fora na propria Elsys."""
    return "erro" not in r and (r.get("rsrp") is None or
                                not (r.get("con") or "").lower().startswith("conectado"))


def veredito_queda(janela, t0):
    """O sinal caiu antes de sumir (obstaculo) ou estava bom ate o fim (Elsys)?

    `janela` sao as leituras de radio em volta do evento em `t0`. A queda conta
    a partir da primeira leitura em que a propria Elsys mostra o LTE fora; antes
    dela, compara a primeira metade (linha de base) com o pior valor."""
    sumiu = next((r for r in janela if _sumiu(r)), None)
    limite = sumiu["t"] if sumiu else t0
    validos = [r["rsrp"] for r in janela
               if r["t"] < limite and "erro" not in r and r.get("rsrp") is not None]
    if len(validos) < 3:
        return "sem leituras de radio suficientes antes da queda"
    metade = validos[:max(1, len(validos) // 2)]
    base = sorted(metade)[len(metade) // 2]
    pior = min(validos)
    if base - pior >= RADIO_QUEDA_DB:
        return (f"o sinal caiu {base - pior:.0f} dB antes da queda (de {base:.0f} para "
                f"{pior:.0f} dBm): obstaculo provavel")
    return (f"o sinal estava estavel antes da queda ({pior:.0f} a {max(validos):.0f} dBm): "
            f"o radio estava bom - aponta para a Elsys")


def relatorio_quedas(pasta, horas=None, offset=None, saida=None, antes=90, depois=30):
    """Para cada wan_perdida, as leituras de radio de `antes` s antes a `depois`
    s depois, e o veredito. O wan_perdida sai do ciclo de 30 s; a janela cobre
    essa folga, e a hora exata da queda vem da propria Elsys."""
    saida = saida or sys.stdout
    evs = [r for r in le_registros(pasta)
           if r.get("tipo") == "evento" and r.get("ev") == "wan_perdida"]
    radio = sorted((r for r in le_registros(pasta, "radio-", "radio.jsonl") if "t" in r),
                   key=lambda r: r["t"])
    if horas:
        fim = max([r["t"] for r in radio] + [e["t"] for e in evs] + [0])
        evs = [e for e in evs if e["t"] >= fim - horas * 3600]

    def hora(t):
        return time.strftime("%d/%m %H:%M:%S", time.localtime(t))

    if not evs:
        print("# nenhuma queda (wan_perdida) no periodo", file=saida)
    if not radio:
        print("# sem leituras de radio (radio.jsonl): radio_s = 0, ou a leitura nunca "
              "funcionou - teste com --radio", file=saida)
    for ev in evs:
        t0 = ev["t"]
        real = f" / {hora(t0 + offset)} real" if offset is not None else ""
        print(f"\n== queda {hora(t0)} (Pi){real} ==", file=saida)
        janela = [r for r in radio if t0 - antes <= r["t"] <= t0 + depois]
        for r in janela:
            fim = r.get("erro") or r.get("con") or ""
            print(f"  {time.strftime('%H:%M:%S', time.localtime(r['t']))} "
                  f"{r['t'] - t0:+5.0f}s  rsrp {r.get('rsrp')}  rsrq {r.get('rsrq')}  {fim}",
                  file=saida)
        print(f"  -> {veredito_queda(janela, t0)}", file=saida)


# ----------------------------------------------------------------- entrada ---

def monta_cfg(args):
    cfg = dict(PADRAO)
    cfg.update(le_conf(args.conf))
    if args.camera:
        cfg["camera"] = args.camera
    for k in ("porta", "intervalo", "rotacao_mb", "manter_arquivos"):
        cfg[k] = int(cfg[k])
    cfg["radio_s"] = float(cfg.get("radio_s") or 0)
    if not cfg["wan"] and cfg["camera"]:
        cfg["wan"] = f"192.168.10.{cfg['camera']}"
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--camera", default="")
    ap.add_argument("--conf", default=CONF)
    ap.add_argument("--uma", action="store_true", help="um ciclo, imprime e sai")
    ap.add_argument("--relatorio", action="store_true")
    ap.add_argument("--horas", type=float, default=None)
    ap.add_argument("--minutos", type=float, default=None,
                    help="janela em minutos (ex.: --minutos 15), no lugar de --horas")
    ap.add_argument("--simples", action="store_true",
                    help="relatorio em frases, para quem nao conhece os eventos")
    ap.add_argument("--dir", default=None, help="pasta dos registros (relatorio)")
    ap.add_argument("--radio", action="store_true",
                    help="le o sinal da Elsys uma vez e imprime (testa o login)")
    ap.add_argument("--quedas", action="store_true",
                    help="RSRP em volta de cada queda (wan_perdida), com veredito")
    ap.add_argument("--offset", type=parse_offset, default=None,
                    help='hora real - hora da Pi: 40d1h4m, segundos, ou '
                         '"AAAA-MM-DD HH:MM:SS=AAAA-MM-DD HH:MM:SS" (Pi=real)')
    args = ap.parse_args(argv)
    cfg = monta_cfg(args)
    if args.minutos:
        args.horas = args.minutos / 60
    if args.simples:
        relatorio_simples(args.dir or cfg["saida"], args.horas, cfg["intervalo"],
                          offset=args.offset)
        return 0
    if args.relatorio:
        relatorio(args.dir or cfg["saida"], args.horas, cfg["intervalo"],
                  offset=args.offset)
        return 0
    if args.quedas:
        relatorio_quedas(args.dir or cfg["saida"], args.horas, offset=args.offset)
        return 0
    if args.radio:
        url = cfg.get("radio_url") or f"http://{cfg['gateway']}/status.html"
        conf = cfg.get("router_conf") or os.path.join(cfg["base"], "router.conf")
        try:
            print(json.dumps(LeitorElsys(url, conf).le(), ensure_ascii=False))
            return 0
        except Exception as e:                               # noqa: BLE001
            print(f"falhou: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    ag = Agente(cfg)
    if args.uma:
        ag.inicio()
        print(json.dumps(ag.ciclo(), indent=1, ensure_ascii=False))
        return 0
    ag.laco()
    return 0


if __name__ == "__main__":
    sys.exit(main())
