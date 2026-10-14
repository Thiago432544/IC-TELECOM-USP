"""Auto-reparo do caminho de rede da camera: o cabo de 14/10, sem ninguem no poste.

O que aconteceu na 102. Em 24/08 04:37 a Elsys parou de encaminhar o trafego
entre a LAN e o LTE: o DHCP dela seguiu respondendo a Pi de 4 em 4 h, mas nada
atravessava - nem o envio do cliente para fora, nem o VNC para dentro. Ficou
assim 50 dias. Em 14/10 alguem tirou e recolocou o cabo do CPE, a Pi refez a
negociacao de endereco do zero e o caminho voltou na hora (diario de 14/10).

Este agente faz o mesmo sozinho. A cada minuto ele pergunta se o caminho ate o
PC do SPA esta de pe; se nao estiver por tempo demais, com o cabo ligado, sobe
uma escada:

    degrau 1  (10 min sem caminho)  renegociar o endereco: `nmcli connection up`
    degrau 2  (20 min sem caminho)  derrubar a placa de rede por 15 s e levantar
                                    - o "cabo" por software
    depois                          nova rodada com espera crescente
                                    (1 h, 2 h, 4 h, 6 h), no maximo N acoes/dia

Separar os dois degraus responde de graca a pergunta que ficou aberta: o que
destravou a Elsys foi a negociacao completa ou o link caindo? O evento
"caminho_ok" diz qual degrau veio antes da volta.

Como ele decide que o caminho esta de pe:
- o cliente tem conexao ESTABLISHED com o servidor: de pe;
- senao, uma conexao de teste a 192.168.10.101:55000 que **abre**: de pe.
  "Recusada" e timeout contam como caminho ruim: o servidor do PC sempre escuta
  nessa porta, entao uma recusa vem de alguem no meio - em 24/08, a propria
  Elsys devolvia RST. Se o problema for o PC desligado, a escada so derruba a
  rede da Pi algumas vezes por dia, o que nao custa nada a uma camera que ja
  nao tem para onde enviar.

O que ele nao faz: reiniciar a Elsys (o restart_router() pelo navegador nao e'
confiavel - tres "sucessos" sem nenhuma queda de link na Pi) nem mexer no
cliente da camera (isso e' o nivel 2). Ele tambem garante que o servico do
RealVNC esteja de pe, porque o objetivo minimo e' nunca perder o acesso.

Protecoes: nada nos primeiros 10 min depois do boot; nada com o cabo
desligado; limite diario; `touch /etc/auto-reparo.pausa` suspende tudo; modo
"observar" registra o que faria sem fazer; a placa de rede nunca fica
desligada - o `up` roda num finally, e na partida o agente religa a interface
se a encontrar desligada.

Os eventos vao para o mesmo registro da caixa-preta (aparecem no --relatorio)
e para o journal: journalctl -u auto-reparo.

Uso:
    sudo python3 auto_reparo.py              # laco (o servico)
    sudo python3 auto_reparo.py --uma        # um ciclo e sai
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
# Na Pi os dois moram em /opt/caixa-preta; no repositorio, em pastas irmas.
for _p in (os.path.join(AQUI, "..", "caixa-preta"), AQUI):
    _p = os.path.abspath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)
import caixa_preta as cp  # noqa: E402 - sondas e leitura de /proc compartilhadas

VERSAO = "2026-10-14"
CONF = "/etc/auto-reparo.conf"
PORTAS_AUTO_REPARO = (55095, 55096, 55097, 55098, 55099)

PADRAO = {
    "camera": "",
    "modo": "ativo",                    # ativo | observar
    "alvo": "192.168.10.101",           # o PC do SPA, pelo encaminhamento da Elsys
    "porta": 55000,
    "gateway": "192.168.11.254",
    "iface": "eth0",
    "conexao_nm": "",                   # vazio = descobre pelo nmcli
    "intervalo": 60,
    "timeout": 8,
    "degrau1_min": 10,
    "degrau2_min": 20,
    "rodadas_min": "60,120,240,360",
    "max_acoes_dia": 8,
    "espera_boot_s": 600,
    "link_fora_s": 15,
    "pausa": "/etc/auto-reparo.pausa",
    "vnc_servico": "vncserver-x11-serviced",
    "saida": "/var/log/caixa-preta",
    "estado": "/var/lib/caixa-preta/auto-reparo.json",
    "proc": "/proc",
    "sys": "/sys",
}
INTEIROS = ("porta", "intervalo", "timeout", "degrau1_min", "degrau2_min",
            "max_acoes_dia", "espera_boot_s", "link_fora_s")
CADA_VNC_S = 300
VNC_REINICIO_MIN_S = 1800


def roda(cmd, timeout=60):
    """subprocess.run que nunca levanta: (codigo, saida+erro)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except (OSError, subprocess.SubprocessError) as e:
        return -1, repr(e)[:300]


def derruba_e_levanta(iface, fora_s, run=roda, dorme=time.sleep):
    """`ip link set down`, espera, `up`. O up esta num finally e e' tentado ate
    tres vezes: a pior falha possivel aqui e' a Pi ficar sem rede de vez."""
    resultado = {"down": run(["ip", "link", "set", iface, "down"], 15)[0]}
    try:
        dorme(fora_s)
    finally:
        for tentativa in range(3):
            cod, _ = run(["ip", "link", "set", iface, "up"], 15)
            resultado["up"] = cod
            if cod == 0:
                break
            dorme(2)
    return resultado


def conexao_nm(iface, run=roda):
    """Nome da conexao ativa do NetworkManager na interface, ou None."""
    cod, saida = run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], 15)
    if cod != 0:
        return None
    for linha in saida.splitlines():
        nome, sep, dev = linha.rpartition(":")
        if sep and dev == iface:
            return nome.replace("\\:", ":")
    return None


class AutoReparo:
    def __init__(self, cfg, agora=time.time, mono=time.monotonic, run=roda,
                 dorme=time.sleep, sonda=None):
        self.cfg = cfg
        self.agora, self.mono, self.run, self.dorme = agora, mono, run, dorme
        self.sonda = sonda or cp.tcp
        self.boot = cp.boot_id(cfg["proc"])[:8]
        self.rodadas = [int(x) for x in str(cfg["rodadas_min"]).split(",") if x.strip()]
        self.acoes = carrega(cfg["estado"]).get("acoes", [])
        self._porta = 0
        self._zera_episodio()
        self.pausado = False
        self.ultimo_vnc_check = -1e9
        self.ultimo_vnc_reinicio = -1e9

    def _zera_episodio(self):
        self.ruim_desde = None          # mono do primeiro teste ruim
        self.rodada = 0
        self.rodada_inicio = 0.0        # em segundos de episodio
        self.feitos = set()
        self.fim_rodada = None
        self.ultimo_degrau = None
        self.ultima_acao = None
        self.avisos = set()             # avisos ja dados neste episodio

    # -- registro: no mesmo arquivo da caixa-preta, e no journal
    def evento(self, nome, **dados):
        r = {"tipo": "evento", "t": round(self.agora(), 1), "mono": round(self.mono(), 1),
             "boot": self.boot, "cam": self.cfg["camera"], "ev": nome,
             "origem": "auto_reparo"}
        r.update(dados)
        linha = json.dumps(r, ensure_ascii=False, separators=(",", ":"))
        print(linha, flush=True)
        try:
            os.makedirs(self.cfg["saida"], exist_ok=True)
            with open(os.path.join(self.cfg["saida"], "atual.jsonl"), "a", encoding="utf-8") as f:
                f.write(linha + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

    def _avisa_uma_vez(self, chave, nome, **dados):
        if chave not in self.avisos:
            self.avisos.add(chave)
            self.evento(nome, **dados)

    # -- o caminho esta de pe?
    def _proxima_porta(self):
        self._porta = (self._porta + 1) % len(PORTAS_AUTO_REPARO)
        return PORTAS_AUTO_REPARO[self._porta]

    def caminho(self):
        cfg = self.cfg
        conns = cp.conexoes_servidor(cfg["proc"], cfg["alvo"], int(cfg["porta"]))
        if any(c["estado"] == "ESTABLISHED" for c in conns):
            return "bom", "cliente_conectado"
        r, ms = self.sonda(cfg["alvo"], int(cfg["porta"]), float(cfg["timeout"]),
                           porta_local=self._proxima_porta())
        if r == "aberta":
            return "bom", f"aberta {ms} ms"
        if r in ("erro:98", "erro:99"):      # porta local ocupada: teste nao vale
            return "indefinido", r
        return "ruim", r

    # -- a escada
    def _proximo_degrau(self, dur):
        d1, d2 = self.cfg["degrau1_min"] * 60, self.cfg["degrau2_min"] * 60
        rel = dur - self.rodada_inicio
        if 1 not in self.feitos and rel >= d1:
            return 1
        if 1 in self.feitos and 2 not in self.feitos and rel >= d2:
            return 2
        if {1, 2} <= self.feitos and self.rodadas:
            espera = self.rodadas[min(self.rodada, len(self.rodadas) - 1)] * 60
            if dur - self.fim_rodada >= espera:
                self.rodada += 1
                self.feitos = set()
                self.rodada_inicio = dur - d1        # degrau 1 ja, degrau 2 em (d2-d1)
                return 1
        return None

    def _acoes_24h(self):
        agora = self.agora()
        self.acoes = [t for t in self.acoes if agora - t < 86400]
        return len(self.acoes)

    def _age(self, degrau, dur, detalhe):
        cfg = self.cfg
        acao = "renegociar_endereco" if degrau == 1 else "derrubar_link"
        contexto = {"degrau": degrau, "acao": acao, "rodada": self.rodada,
                    "fora_s": round(dur), "teste": detalhe,
                    "gw_ms": cp.ping_ms(cfg["gateway"])}
        if self._acoes_24h() >= int(cfg["max_acoes_dia"]):
            self._avisa_uma_vez(("limite", self.rodada, degrau), "reparo_pulado",
                                motivo="limite_diario", **contexto)
            return
        self.feitos.add(degrau)
        if degrau == 2:
            self.fim_rodada = dur
        if cfg["modo"] != "ativo":
            self.evento("reparo_teria_agido", **contexto)
            return
        if degrau == 1:
            nome = cfg["conexao_nm"] or conexao_nm(cfg["iface"], self.run)
            if nome:
                resultado = self.run(["nmcli", "connection", "up", nome], 90)
                resultado = {"codigo": resultado[0], "saida": resultado[1]}
            else:
                resultado = {"codigo": None, "saida": "sem conexao do NetworkManager"}
        else:
            resultado = derruba_e_levanta(cfg["iface"], int(cfg["link_fora_s"]),
                                          self.run, self.dorme)
        self.acoes.append(self.agora())
        salva(cfg["estado"], {"acoes": self.acoes})
        self.ultimo_degrau, self.ultima_acao = degrau, self.mono()
        self.evento("reparo", resultado=resultado, **contexto)

    # -- o servico do VNC
    def _vnc(self):
        srv = self.cfg["vnc_servico"]
        m = self.mono()
        if not srv or m - self.ultimo_vnc_check < CADA_VNC_S:
            return
        self.ultimo_vnc_check = m
        cod, estado = self.run(["systemctl", "is-active", srv], 15)
        if estado.strip() == "active":
            return
        _, habilitado = self.run(["systemctl", "is-enabled", srv], 15)
        if habilitado.strip() != "enabled":
            return                              # nao instalado ou desligado de proposito
        if m - self.ultimo_vnc_reinicio < VNC_REINICIO_MIN_S:
            return
        self.ultimo_vnc_reinicio = m
        if self.cfg["modo"] != "ativo":
            self.evento("vnc_teria_reiniciado", estado=estado.strip())
            return
        cod, saida = self.run(["systemctl", "restart", srv], 60)
        self.evento("vnc_reiniciado", estado_anterior=estado.strip(), codigo=cod, saida=saida)

    # -- um ciclo
    def passo(self):
        cfg = self.cfg
        if os.path.exists(cfg["pausa"]):
            if not self.pausado:
                self.pausado = True
                self.evento("pausado", arquivo=cfg["pausa"])
            return "pausa"
        if self.pausado:
            self.pausado = False
            self.evento("retomado")
        self._vnc()
        up = cp.uptime_s(cfg["proc"])
        if up is not None and up < int(cfg["espera_boot_s"]):
            return "boot"
        estado, detalhe = self.caminho()
        if estado == "indefinido":
            return estado
        m = self.mono()
        if estado == "bom":
            if self.ruim_desde is not None:
                self.evento("caminho_ok", fora_s=round(m - self.ruim_desde), teste=detalhe,
                            resolvido_por=self.ultimo_degrau,
                            desde_ultimo_reparo_s=(round(m - self.ultima_acao)
                                                   if self.ultima_acao else None))
            self._zera_episodio()
            return "bom"
        if self.ruim_desde is None:
            # o ultimo teste bom foi um intervalo atras: conta dali, para que
            # "10 min" signifique 10 testes ruins seguidos
            self.ruim_desde = m - float(cfg["intervalo"])
            self.evento("caminho_ruim", teste=detalhe,
                        gw_ms=cp.ping_ms(cfg["gateway"]))
        dur = m - self.ruim_desde
        carrier = cp.interface(cfg["proc"], cfg["sys"], cfg["iface"]).get("carrier")
        if carrier != "1":
            self._avisa_uma_vez("cabo", "reparo_pulado", motivo="cabo_desligado",
                                carrier=carrier, fora_s=round(dur))
            return "ruim"
        degrau = self._proximo_degrau(dur)
        if degrau:
            self._age(degrau, dur, detalhe)
        return "ruim"

    def garante_interface_ligada(self):
        """Se o agente morreu no meio de um degrau 2, a placa pode ter ficado down."""
        flags = (cp.ler(os.path.join(self.cfg["sys"], "class/net", self.cfg["iface"], "flags"), "")
                 or "").strip()
        try:
            ligada = int(flags, 16) & 0x1
        except ValueError:
            return
        if not ligada:
            cod, saida = self.run(["ip", "link", "set", self.cfg["iface"], "up"], 15)
            self.evento("interface_estava_desligada", codigo=cod, saida=saida)

    def laco(self):
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))   # o finally do degrau 2 roda
        self.evento("auto_reparo_inicio", versao=VERSAO, modo=self.cfg["modo"],
                    alvo=f'{self.cfg["alvo"]}:{self.cfg["porta"]}', iface=self.cfg["iface"],
                    acoes_24h=self._acoes_24h())
        self.garante_interface_ligada()
        intervalo = float(self.cfg["intervalo"])
        while True:
            try:
                self.passo()
            except Exception as e:                       # noqa: BLE001 - nunca para
                self.evento("erro", erro=repr(e)[:500])
            self.dorme(intervalo)


def carrega(caminho):
    try:
        with open(caminho) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def salva(caminho, dados):
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        tmp = caminho + ".tmp"
        with open(tmp, "w") as f:
            json.dump(dados, f)
        os.replace(tmp, caminho)
    except OSError:
        pass


def monta_cfg(conf=CONF):
    cfg = dict(PADRAO)
    cfg.update(cp.le_conf(conf))
    for k in INTEIROS:
        cfg[k] = int(cfg[k])
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--conf", default=CONF)
    ap.add_argument("--uma", action="store_true", help="um ciclo, imprime e sai")
    args = ap.parse_args(argv)
    ag = AutoReparo(monta_cfg(args.conf))
    if args.uma:
        print("caminho:", ag.caminho(), "| passo:", ag.passo())
        return 0
    ag.laco()
    return 0


if __name__ == "__main__":
    sys.exit(main())
