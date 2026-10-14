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
relogio da Pi esta errado em duas das tres cameras; a hora "real" sai do
cabecalho Date do web server do CPE, registrado a cada 10 min como evento
"relogio" (offset = Date do CPE - relogio da Pi).

Principio da spec: o monitoramento nunca pode causar a falha. Por isso: so
stdlib, somente leitura sobre tudo do cliente, nenhuma conexao com o servidor
enquanto o cliente esta conectado, e falha propria vira evento "erro" - o laco
nunca para.

Uso:
    sudo python3 caixa_preta.py --camera 102            # laco (o servico)
    sudo python3 caixa_preta.py --camera 102 --uma      # um ciclo, imprime e sai
    python3 caixa_preta.py --relatorio                  # linha do tempo dos eventos
    python3 caixa_preta.py --relatorio --horas 6        # so as ultimas 6 h
"""
import argparse
import email.utils
import glob
import http.client
import json
import os
import socket
import struct
import subprocess
import sys
import time
import traceback

VERSAO = "2026-10-14"

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
}
CONF = "/etc/caixa-preta.conf"

# Cadencias, em segundos. O CPE e' o equipamento suspeito: sonda-lo de leve.
CADA_CPE_TCP = 60
CADA_CPE_HTTP = 600
CADA_SERVIDOR = 300            # so quando o cliente NAO esta conectado
CADA_VCGENCMD = 300
CICLOS_FILA_PRESA = 4          # 4 x 30 s com bytes parados na fila de envio

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


def conexoes_servidor(proc, ip, porta, ignorar_portas=()):
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


def tcp(ip, porta, timeout=5.0, portas_locais=None):
    """('aberta'|'recusada'|'timeout'|'erro:<errno>', ms). A porta local usada
    vai para `portas_locais`, para a sonda nao aparecer como conexao do cliente."""
    t0 = time.monotonic()
    try:
        s = socket.create_connection((ip, porta), timeout)
        if portas_locais is not None:
            portas_locais.append(s.getsockname()[1])
            del portas_locais[:-20]
        s.close()
        r = "aberta"
    except ConnectionRefusedError:
        r = "recusada"
    except (socket.timeout, TimeoutError):
        r = "timeout"
    except OSError as e:
        r = f"erro:{e.errno}"
    return r, round((time.monotonic() - t0) * 1000)


def http_data(ip, timeout=8.0):
    """(status, Date do CPE em epoch) do GET /. O CPE tem hora da rede LTE;
    a Pi nao. E' a referencia para corrigir o relogio dos registros."""
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

    def __init__(self, pasta, rotacao_mb=20, manter=100):
        self.pasta = pasta
        self.limite = int(rotacao_mb) * 1024 * 1024
        self.manter = int(manter)
        os.makedirs(pasta, exist_ok=True)
        self.atual = os.path.join(pasta, "atual.jsonl")

    def grava(self, reg):
        linha = json.dumps(reg, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.atual, "a", encoding="utf-8") as f:
            f.write(linha)
            f.flush()
            os.fsync(f.fileno())
        if os.path.getsize(self.atual) > self.limite:
            self._roda()

    def _roda(self):
        nome = os.path.join(self.pasta, time.strftime("cp-%Y%m%d-%H%M%S.jsonl"))
        while os.path.exists(nome):
            nome = nome.replace(".jsonl", "_.jsonl")
        os.replace(self.atual, nome)
        antigos = sorted(glob.glob(os.path.join(self.pasta, "cp-*.jsonl")),
                         key=os.path.getmtime)
        for p in antigos[:-self.manter] if self.manter else []:
            try:
                os.unlink(p)
            except OSError:
                pass


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
        self.portas_sonda = []

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
        a["conn"] = conexoes_servidor(cfg["proc"], cfg["servidor"], int(cfg["porta"]),
                                      self.portas_sonda)
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
                                portas_locais=self.portas_sonda)

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

    def laco(self):
        self.inicio()
        intervalo = float(self.cfg["intervalo"])
        prox = self.mono()
        while True:
            try:
                self.ciclo()
            except Exception:                                # noqa: BLE001
                try:
                    self.evento("erro", tb=traceback.format_exc()[-1500:])
                except Exception:                            # noqa: BLE001
                    pass
            prox += intervalo
            espera = prox - self.mono()
            if espera < 0:
                prox = self.mono()
                espera = 0
            time.sleep(espera)


# --------------------------------------------------------------- relatorio ---

def le_registros(pasta):
    arquivos = sorted(glob.glob(os.path.join(pasta, "cp-*.jsonl")), key=os.path.getmtime)
    arquivos.append(os.path.join(pasta, "atual.jsonl"))
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


def relatorio(pasta, horas=None, intervalo=30, saida=None):
    """Linha do tempo: eventos, lacunas entre amostras e reboots, com hora da
    Pi e hora real estimada. O offset usado em cada linha e' o da leitura de
    relogio mais recente ate ali (o NTP pode acertar a Pi no meio do caminho);
    antes da primeira leitura, vale a primeira."""
    saida = saida or sys.stdout
    regs = list(le_registros(pasta))
    offsets = [r["offset_s"] for r in regs
               if r.get("ev") == "relogio" and r.get("offset_s") is not None]
    offset = offsets[0] if offsets else 0.0
    if horas:
        corte = max((r.get("t", 0) for r in regs), default=0) - horas * 3600
        regs = [r for r in regs if r.get("t", 0) >= corte]

    def hora(t):
        return time.strftime("%d/%m %H:%M:%S", time.localtime(t))

    final = offsets[-1] if offsets else 0.0
    print(f"# {len(regs)} registros. Offset do relogio (Date do CPE - Pi): "
          f"{final:+.0f} s = {final / 86400:+.2f} dias", file=saida)
    print("# hora da Pi        | hora real estimada | evento", file=saida)
    ultima = None
    for r in regs:
        t = r.get("t", 0)
        if r.get("ev") == "relogio" and r.get("offset_s") is not None:
            offset = r["offset_s"]
        if r.get("tipo") == "amostra":
            if ultima and ultima.get("boot") == r.get("boot") and \
                    r.get("mono", 0) - ultima.get("mono", 0) > 3 * intervalo:
                buraco = r["mono"] - ultima["mono"]
                print(f"{hora(t)} | {hora(t + offset)} | LACUNA de {buraco:.0f} s sem "
                      f"amostra (agente ou sistema parado)", file=saida)
            ultima = r
            continue
        dados = {k: v for k, v in r.items()
                 if k not in ("tipo", "t", "mono", "boot", "cam", "ev")}
        texto = json.dumps(dados, ensure_ascii=False)
        if len(texto) > 400:
            texto = texto[:400] + "..."
        print(f"{hora(t)} | {hora(t + offset)} | {r.get('ev')} {texto}", file=saida)
    if ultima:
        print(f"# ultima amostra: {hora(ultima['t'])} (Pi) / {hora(ultima['t'] + offset)} "
              f"(real). gw={ultima.get('gw_ms')} conn="
              f"{[c['estado'] for c in ultima.get('conn', [])]} "
              f"cliente={[c['pid'] for c in ultima.get('cliente', [])]}", file=saida)


# ----------------------------------------------------------------- entrada ---

def monta_cfg(args):
    cfg = dict(PADRAO)
    cfg.update(le_conf(args.conf))
    if args.camera:
        cfg["camera"] = args.camera
    for k in ("porta", "intervalo", "rotacao_mb", "manter_arquivos"):
        cfg[k] = int(cfg[k])
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
    ap.add_argument("--dir", default=None, help="pasta dos registros (relatorio)")
    args = ap.parse_args(argv)
    cfg = monta_cfg(args)
    if args.relatorio:
        relatorio(args.dir or cfg["saida"], args.horas, cfg["intervalo"])
        return 0
    ag = Agente(cfg)
    if args.uma:
        ag.inicio()
        print(json.dumps(ag.ciclo(), indent=1, ensure_ascii=False))
        return 0
    ag.laco()
    return 0


if __name__ == "__main__":
    sys.exit(main())
