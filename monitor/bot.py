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
         "/metricas - o que da para plotar")

_FASE3 = "sem dados: depende do agente da Fase 3 nas Rasps"


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
        linhas.append(f"- {m.key} ({m.label}){marca}")
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
        if cam not in self.cfg.cameras or spec is None or not janela.isdigit():
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
        if cam is None:
            return Reply(_HELP)
        if sobra:
            return Reply(f"Nao conheco '{sobra[0]}'. Veja /metricas.")
        # Primeira duracao e' a janela; uma segunda force o piso na mao.
        janela = duracoes[0] if duracoes else DEFAULT_WINDOW
        forcado = duracoes[1] if len(duracoes) > 1 else None
        return self._desenhar(cam, spec or find_metric("conexao"), janela, now,
                              forcado)

    def _desenhar(self, cam: str, spec: MetricSpec, janela: float, now: float,
                  forcado: Optional[float] = None) -> Reply:
        if forcado is None and self.cfg.charts.outage_min_s:
            forcado = self.cfg.charts.outage_min_s
        piso = outage_floor(janela, forcado)
        png = render_metric_chart(self.store, cam, spec, now, janela, piso)
        return Reply(self._legenda(cam, spec, janela, piso, now), png,
                     _botoes(cam, spec, janela))

    def _legenda(self, cam: str, spec: MetricSpec, janela: float, piso: int,
                 now: float) -> str:
        if spec.kind != "outages":
            cabec = f"{cam} · {spec.label} · {label_duration(janela)}"
            return f"{cabec}\n{_FASE3}" if spec.phase == 3 else cabec
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
                 f'quedas >={piso}:']
        for cam, c in sorted(st["cameras"].items()):
            fpm = f'{c["frames_min"]:.1f} f/min' if c["frames_min"] is not None else "-"
            age = (f'{c["last_frame_age_s"]:.0f}s'
                   if c["last_frame_age_s"] is not None else "-")
            no_ar = ("sem dados" if c["uptime_24h"] is None
                     else f'no ar {c["uptime_24h"]}%')
            n = c["outages_24h"]
            quedas = "sem quedas" if n == 0 else f"{n} queda{'s' if n > 1 else ''}"
            lines.append(f'- {cam} {icon[c["state"]]} | {no_ar} | {quedas} '
                         f'| ult. frame {age} | {fpm}')
        if st["disk_free_gb"] is not None:
            lines.append(f'Disco D: {st["disk_free_gb"]:.0f} GB livres')
        if st["rsrp"] is not None:
            lines.append(f'Enlace: RSRP {st["rsrp"]:.0f} dBm')
        return "\n".join(lines)
