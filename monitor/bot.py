"""Comandos do bot: /status, /metricas e /grafico <camera> [metrica] [janela].

Os tokens do /grafico sao lidos pelo formato, nao pela posicao: no celular a
gente digita na ordem que vier.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional

from monitor.charts import caption, fmt_dur, render_metric_chart
from monitor.config import Config
from monitor.metrics import (BUTTON_WINDOWS, DEFAULT_WINDOW, METRICS,
                             MetricSpec, find_metric, label_duration,
                             outage_floor, parse_duration)
from monitor.panel import build_status
from monitor.store import Store
from monitor.uptime import availability, coverage_gaps, outages

_HELP = ("Comandos:\n"
         "/status - estado atual das cameras\n"
         "/grafico <camera> [metrica] [janela] - ex.: /grafico 106 conexao 2h\n"
         "/grafico rsrp | rsrq | disco [janela] - metricas que nao sao de camera\n"
         "/metricas - o que da para plotar")

_FASE3 = "sem dados: depende do agente da Fase 3 nas Rasps"
_PROBE = "Calibre com: python -m monitor.cpe --probe"
_CPE_OFF = "sem dados: [cpe] enabled = false no config.toml. " + _PROBE
_CPE_MUDO = ("sem dados: o CPE nao respondeu, ou rsrp_re/rsrq_re nao casam "
             "com a pagina. " + _PROBE)


@dataclass(frozen=True)
class Reply:
    text: str
    png: Optional[bytes] = None
    buttons: Optional[list[tuple[str, str]]] = None


def _botoes(camera: str, spec: MetricSpec, atual: float):
    """Marca a janela vigente para o botao nao virar adivinhacao."""
    return [(("•" if w == atual else "") + label_duration(w),
             f"g:{camera}:{spec.key}:{int(w)}")
            for w in BUTTON_WINDOWS]


def _lista_metricas() -> str:
    linhas = ["Metricas (/grafico <camera> <metrica> [janela]):"]
    for m in METRICS:
        marca = "  [Fase 3, ainda sem dados]" if m.phase == 3 else ""
        # onde nao ha camera, o numero da camera e' opcional no comando
        de = "" if m.origin == "camera" else f"  [{m.origin}, sem camera]"
        linhas.append(f"- {m.key} ({m.label}){de}{marca}")
    linhas.append("Janelas: 30min, 1h, 2h, 12h, 24h, 7d")
    return "\n".join(linhas)


class BotHandler:
    def __init__(self, store: Store, cfg: Config):
        self.store = store
        self.cfg = cfg

    # -- entrada ---------------------------------------------------------
    def handle(self, text: str, now: float) -> Reply:
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""
        if cmd == "/status":
            return Reply(self._status(now))
        if cmd == "/metricas":
            return Reply(_lista_metricas())
        if cmd == "/grafico":
            return self._grafico(parts[1:], now)
        return Reply(_HELP)

    def handle_callback(self, data: str, now: float) -> Reply:
        partes = (data or "").split(":")
        if len(partes) != 4 or partes[0] != "g":
            return Reply(_HELP)
        _, cam, metrica, janela = partes
        spec = find_metric(metrica)
        if spec is None or not janela.isdigit():
            return Reply(_HELP)
        if cam != self._sujeito(cam, spec):
            return Reply(_HELP)
        if spec.origin == "camera" and cam not in self.cfg.cameras:
            return Reply(_HELP)
        return self._desenhar(cam, spec, int(janela), now)

    # -- /grafico --------------------------------------------------------
    def _grafico(self, toks: list[str], now: float) -> Reply:
        cam, spec, duracoes, sobra = None, None, [], []
        for t in toks:
            if cam is None and t in self.cfg.cameras:
                cam = t
                continue
            d = parse_duration(t)
            if d is not None:
                duracoes.append(d)
                continue
            m = find_metric(t)
            if spec is None and m is not None:
                spec = m
                continue
            sobra.append(t)
        # RSRP/RSRQ sao do CPE e o disco e' do PC: nao pertencem a camera
        # nenhuma, entao exigir o numero era pedir um dado que nao existe.
        if cam is None and (spec is None or spec.origin == "camera"):
            return Reply(_HELP)
        if sobra:
            return Reply(f"Nao conheco '{sobra[0]}'. Veja /metricas.")
        # Primeira duracao e' a janela; uma segunda force o piso na mao.
        janela = duracoes[0] if duracoes else DEFAULT_WINDOW
        forcado = duracoes[1] if len(duracoes) > 1 else None
        spec = spec or find_metric("conexao")
        return self._desenhar(self._sujeito(cam, spec), spec, janela, now,
                              forcado)

    def _sujeito(self, cam, spec: MetricSpec) -> str:
        """De quem e' o dado. RSRP/RSRQ sao do CPE do lado do SPA e o disco e'
        do PC: carimbar '106' neles faria parecer o radio da Rasp."""
        return (cam or "") if spec.origin == "camera" else spec.origin

    def _sem_dados(self, alvo: str, spec: MetricSpec, since: float,
                   now: float) -> Optional[str]:
        """Por que o grafico saiu vazio. "sem dados" sozinho manda tentar
        outra janela quando o problema e' o coletor desligado."""
        if spec.phase == 3:
            return _FASE3
        if self.store.samples(alvo, spec.sample, since, now):
            return None
        if spec.origin == "cpe":
            return _CPE_OFF if not self.cfg.cpe.enabled else _CPE_MUDO
        return None

    def _desenhar(self, cam: str, spec: MetricSpec, janela: float, now: float,
                  forcado: Optional[float] = None) -> Reply:
        if forcado is None and self.cfg.charts.outage_min_s:
            forcado = self.cfg.charts.outage_min_s
        piso = outage_floor(janela, forcado)
        nota = (None if spec.kind == "outages"
                else self._sem_dados(cam, spec, now - janela, now))
        png = render_metric_chart(self.store, cam, spec, now, janela, piso,
                                  nota)
        return Reply(self._legenda(cam, spec, janela, piso, now, nota), png,
                     _botoes(cam, spec, janela))

    def _legenda(self, cam: str, spec: MetricSpec, janela: float, piso: int,
                 now: float, nota: Optional[str] = None) -> str:
        if spec.kind != "outages":
            cabec = f"{cam} \u00b7 {spec.label} \u00b7 {label_duration(janela)}"
            return f"{cabec}\n{nota}" if nota else cabec
        since = now - janela
        outs = outages(self.store, cam, since, now, piso)
        gaps = coverage_gaps(self.store, cam, since, now)
        linhas = [caption(cam, janela, piso, outs,
                          availability(outs, since, now, tuple(gaps)))]
        for o in sorted(outs, key=lambda o: -o.duration_s)[:3]:
            quando = time.strftime("%d/%m %H:%M", time.localtime(o.start))
            motivo = f"  {o.reason}" if o.reason else ""
            linhas.append(f"{quando}  {fmt_dur(o.duration_s)}{motivo}")
        return "\n".join(linhas)

    # -- /status ---------------------------------------------------------
    def _status(self, now: float) -> str:
        st = build_status(self.store, self.cfg, now)
        icon = {"ok": "OK", "atrasada": "ATRASADA", "sem_dados": "SEM DADOS"}
        piso = label_duration(st["outage_floor_s"])
        # Janela e piso no cabecalho, uma vez, em vez de repetidos por linha.
        lines = [f'Estado atual - ultimas {label_duration(st["window_s"])}, '
                 f'intervalos sem imagem >={piso}:']
        for cam, c in sorted(st["cameras"].items()):
            fpm = f'{c["frames_min"]:.1f} f/min' if c["frames_min"] is not None else "-"
            age = (f'{c["last_frame_age_s"]:.0f}s'
                   if c["last_frame_age_s"] is not None else "-")
            img = ("sem dados" if c["uptime_24h"] is None
                   else f'imagem {c["uptime_24h"]}%')
            n = c["outages_24h"]
            gaps = ("sem intervalo" if n == 0
                    else f"{n} intervalo{'s' if n > 1 else ''}")
            lines.append(f'- {cam} {icon[c["state"]]} | {img} | {gaps} '
                         f'| ult. frame {age} | {fpm}')
        if st["disk_free_gb"] is not None:
            lines.append(f'Disco D: {st["disk_free_gb"]:.0f} GB livres')
        radio = []
        if st["rsrp"] is not None:
            radio.append(f'RSRP {st["rsrp"]:.0f} dBm')
        if st["rsrq"] is not None:
            radio.append(f'RSRQ {st["rsrq"]:.0f} dB')
        if radio:
            lines.append("Enlace: " + "  ".join(radio))
        elif not st["cpe_enabled"]:
            # sem esta linha o /status apenas omite o enlace, e quem le supoe
            # que ele esta bem - quando na verdade ninguem esta medindo
            lines.append("Enlace: CPE desligado ([cpe] enabled = false). "
                         "Calibre com: python -m monitor.cpe --probe")
        else:
            lines.append("Enlace: CPE ligado, mas sem leitura de RSRP/RSRQ.")
        return "\n".join(lines)
