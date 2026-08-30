"""Registra o radio do CPE da camera num CSV, de dentro da propria Raspberry.

Ate 28/08/2026 o RSRP/RSRQ do lado da camera era dado como observavel so pela
Fase 3, com agente e push. Nao e': o RUT200 da 105 tem API REST, e a Pi alcanca
o proprio CPE em 192.168.11.1 - o PC do SPA nao, porque esse endereco e' a LAN
do radio, atras dele. Este e' o coletor minimo: roda na Pi, escreve uma linha
por amostra, e nao depende de nada do lado do SPA.

As credenciais saem do mesmo router.conf que o watchdog_test.py le, para nao
existir segredo duplicado nem versionado.

So stdlib, de proposito: o principio da spec e' que o monitoramento nunca pode
ser a causa da falha, e instalar dependencia numa maquina de campo e' risco sem
necessidade.

Cuidado ao ler o numero: o RSRP satura em -44 dBm no padrao 3GPP. A primeira
leitura da 105, em 28/08, veio -45 com RSSI -47 - e RSRP acima de RSSI e'
impossivel, entao o modulo esta entregando o topo da escala. "Forte demais para
medir" e' a leitura honesta; nao trate -45 como valor exato.

Uso, na pasta onde esta o router.conf:
    python3 radio_log.py --once        # uma amostra, para conferir
    python3 radio_log.py               # laco, uma amostra por minuto
    python3 radio_log.py --intervalo 300 --csv /home/pi/radio.csv
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(BASE, "router.conf")
CSV_PADRAO = os.path.join(BASE, "radio.csv")

# Campos gravados, na ordem. Os tres primeiros sao o que se quer medir; o resto
# e' o que permite cruzar com a eNodeB (celula, banda) e com o campo (temperatura).
COLUNAS = ("ts", "rsrp", "rsrq", "sinr", "rssi", "band", "earfcn", "pcid",
           "cellid", "tac", "temperatura", "netstate", "conntype",
           "rxbytes", "txbytes")

# O token do RUT200 dura 299 s. Renovar com folga evita perder uma amostra na
# virada, que e' justamente quando o enlace costuma estar ruim.
MARGEM_TOKEN_S = 60


def le_conf(caminho=CONF):
    """url do radio, usuario e senha. Mesmo formato do watchdog_test.py."""
    cfg = {"url_api": "http://192.168.11.1", "usuario": "", "senha": ""}
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


def _post_json(url, corpo, cabecalhos=None, timeout=10):
    dados = json.dumps(corpo).encode()
    req = urllib.request.Request(url, data=dados, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **(cabecalhos or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_json(url, cabecalhos=None, timeout=10):
    req = urllib.request.Request(url, headers=cabecalhos or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _num(v):
    """Converte para float; 'N/A' e vazio viram None.

    O modulo devolve 'N/A' nos campos da tecnologia que nao esta em uso - rscp
    e ecio ficam 'N/A' em LTE. Gravar a string estragaria a serie inteira.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extrai(status):
    """Do JSON de /api/modems/status para uma linha do CSV.

    Usa os campos de topo, e nao os de cell_info: na leitura de 28/08 o
    cell_info trazia rsrp/rsrq/sinr como 'N/A' enquanto o topo tinha os valores.
    """
    d = (status.get("data") or [{}])[0]
    celula = (d.get("cell_info") or [{}])[0]
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rsrp": _num(d.get("rsrp")),
        "rsrq": _num(d.get("rsrq")),
        "sinr": _num(d.get("sinr")),
        "rssi": _num(d.get("rssi")),
        "band": d.get("band"),
        "earfcn": celula.get("earfcn"),
        "pcid": celula.get("pcid"),
        "cellid": d.get("cellid"),
        "tac": d.get("tac"),
        "temperatura": _num(d.get("temperature")),
        "netstate": d.get("netstate"),
        "conntype": d.get("conntype"),
        "rxbytes": _num(d.get("rxbytes")),
        "txbytes": _num(d.get("txbytes")),
    }


class Radio:
    """Cliente do RUT200. post/get injetaveis para o teste nao precisar de rede."""

    def __init__(self, cfg, post=None, get=None, agora=time.time):
        self.cfg = cfg
        self._post = post or _post_json
        self._get = get or _get_json
        self._agora = agora
        self._token = None
        self._vence = 0.0

    def _entra(self):
        r = self._post(self.cfg["url_api"] + "/api/login",
                       {"username": self.cfg["usuario"],
                        "password": self.cfg["senha"]})
        dados = r.get("data") or {}
        token = dados.get("token")
        if not token:
            raise RuntimeError(f"login recusado: {r.get('errors') or r}")
        self._token = token
        self._vence = self._agora() + float(dados.get("expires", 299))

    def token(self):
        if self._token is None or self._agora() > self._vence - MARGEM_TOKEN_S:
            self._entra()
        return self._token

    def amostra(self):
        status = self._get(self.cfg["url_api"] + "/api/modems/status",
                           {"Authorization": "Bearer " + self.token()})
        if not status.get("success", True):
            # token revogado ou expirado antes da hora: uma segunda chance
            self._token = None
            status = self._get(self.cfg["url_api"] + "/api/modems/status",
                               {"Authorization": "Bearer " + self.token()})
        return extrai(status)


def grava(linha, caminho):
    novo = not os.path.exists(caminho)
    with open(caminho, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        if novo:
            w.writeheader()
        w.writerow(linha)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Registra o radio do CPE num CSV")
    ap.add_argument("--once", action="store_true", help="uma amostra e sai")
    ap.add_argument("--intervalo", type=float, default=60.0)
    ap.add_argument("--csv", default=CSV_PADRAO)
    ap.add_argument("--conf", default=CONF)
    args = ap.parse_args(argv)

    cfg = le_conf(args.conf)
    if not cfg["senha"]:
        print(f"sem credencial em {args.conf}", file=sys.stderr)
        return 2
    radio = Radio(cfg)

    while True:
        try:
            linha = radio.amostra()
            grava(linha, args.csv)
            print(f"{linha['ts']}  RSRP {linha['rsrp']}  RSRQ {linha['rsrq']}  "
                  f"SINR {linha['sinr']}  {linha['band']}  {linha['netstate']}")
        except Exception as e:
            # Nunca sair do laco por falha de rede: quando o enlace cai e' quando
            # a amostra seguinte mais importa.
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  falhou: {e}",
                  file=sys.stderr)
        sys.stdout.flush()
        if args.once:
            return 0
        time.sleep(args.intervalo)


if __name__ == "__main__":
    raise SystemExit(main())
