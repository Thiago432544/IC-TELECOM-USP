"""Graficos PNG para os alertas e o comando /grafico.

O grafico de conexao e' uma faixa: cheio = entregando, vazado = fora, cinza =
o monitor nao estava no ar. Ate 24h e' uma faixa so; acima disso vira uma
linha por dia, para bater o mesmo horario de um dia contra o outro.
"""
from __future__ import annotations
import io
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from monitor.metrics import MetricSpec, label_duration
from monitor.store import Store
from monitor.uptime import Outage, availability, coverage_gaps, outages

DIA = 86400

COR_UP = "#2f9e44"
COR_DOWN = "#e03131"
COR_UNK = "#ced4da"


@dataclass(frozen=True)
class BandRow:
    label: str
    start: float
    end: float


def fmt_dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}min"
    if s < DIA:
        h, m = divmod(s // 60, 60)
        return f"{h}h{m:02d}" if m else f"{h}h"
    d, h = divmod(s // 3600, 24)
    return f"{d}d{h}h" if h else f"{d}d"


def band_rows(since: float, until: float) -> list[BandRow]:
    """Uma faixa ate 24h; uma por dia local acima disso."""
    if until - since <= DIA:
        return [BandRow("", since, until)]
    t = datetime.fromtimestamp(since).replace(hour=0, minute=0, second=0,
                                              microsecond=0)
    rows = []
    while t.timestamp() < until:
        rows.append(BandRow(t.strftime("%d/%m"), t.timestamp(),
                            t.timestamp() + DIA))
        t += timedelta(days=1)
    return rows


def caption(camera: str, window_s: float, floor_s: float,
            outs: list[Outage], avail: float) -> str:
    piso = label_duration(floor_s)
    partes = [f"{camera} · {label_duration(window_s)}",
              "sem dados" if avail is None else f"no ar {avail}%"]
    if outs:
        n = len(outs)
        partes.append(f"{n} queda{'s' if n > 1 else ''} >={piso}")
        partes.append(f"maior {fmt_dur(max(o.duration_s for o in outs))}")
    else:
        partes.append(f"sem queda >={piso}")
    return "  ·  ".join(partes)


def _spans_offset(spans, row: BandRow, since: float, until: float):
    """Recorta os trechos na interseccao da linha com a janela, em offset."""
    lo, hi = max(row.start, since), min(row.end, until)
    out = []
    for a, b in spans:
        a2, b2 = max(a, lo), min(b, hi)
        if b2 > a2:
            out.append((a2 - row.start, b2 - a2))
    return out


def _eixo_x(ax, since, until, calendario):
    if calendario:
        ax.set_xlim(0, DIA)
        ax.set_xticks(range(0, DIA + 1, 4 * 3600))
        ax.set_xticklabels([f"{h:02d}h" for h in range(0, 25, 4)])
    else:
        largura = until - since
        ax.set_xlim(0, largura)
        pos = [largura * i / 6 for i in range(7)]
        ax.set_xticks(pos)
        ax.set_xticklabels([time.strftime("%H:%M", time.localtime(since + p))
                            for p in pos])


def _render_outages(store: Store, camera: str, now: float,
                    window_s: float, floor_s: float) -> bytes:
    since, until = now - window_s, now
    outs = outages(store, camera, since, until, floor_s)
    gaps = coverage_gaps(store, camera, since, until)
    avail = availability(outs, since, until, tuple(gaps))
    rows = band_rows(since, until)
    calendario = len(rows) > 1
    alvo = [(o.start, o.end if o.end is not None else until) for o in outs]

    fig, ax = plt.subplots(figsize=(9, 2.0 + 0.42 * len(rows)), dpi=110)
    altura = 0.6
    for i, r in enumerate(rows):
        y = len(rows) - 1 - i
        base = _spans_offset([(since, until)], r, since, until)
        ax.broken_barh(base, (y - altura / 2, altura), facecolors=COR_UP)
        ax.broken_barh(_spans_offset(gaps, r, since, until),
                       (y - altura / 2, altura), facecolors=COR_UNK)
        ax.broken_barh(_spans_offset(alvo, r, since, until),
                       (y - altura / 2, altura), facecolors=COR_DOWN)

    ax.set_ylim(-0.6, len(rows) - 0.4)
    if calendario:
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r.label for r in reversed(rows)])
    else:
        ax.set_yticks([])
    ax.tick_params(axis="y", length=0)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    _eixo_x(ax, since, until, calendario)
    ax.set_title(f"Camera {camera} - conexao\n"
                 + caption(camera, window_s, floor_s, outs, avail),
                 fontsize=10)
    # Legenda em coordenada de figura: ancorada no eixo ela se afasta cada vez
    # mais conforme o numero de linhas cresce.
    fig.legend(handles=[Patch(facecolor=COR_UP, label="no ar"),
                        Patch(facecolor=COR_DOWN, label="fora"),
                        Patch(facecolor=COR_UNK, label="sem dados")],
               loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.subplots_adjust(bottom=0.30 if len(rows) == 1 else 0.14)
    return _png(fig)


def _render_series(store: Store, origin: str, spec: MetricSpec,
                   now: float, window_s: float) -> bytes:
    since = now - window_s
    alvo = origin if spec.origin == "camera" else spec.origin
    rows = store.samples(alvo, spec.sample, since, now)

    fig, ax = plt.subplots(figsize=(9, 4), dpi=110)
    ax.set_title(f"{origin} - {spec.label} - {label_duration(window_s)}",
                 fontsize=10)
    ax.set_ylabel(f"{spec.label} ({spec.unit})")
    if rows:
        ax.plot([datetime.fromtimestamp(t) for t, _ in rows],
                [v for _, v in rows], lw=1.6)
        fig.autofmt_xdate()
    else:
        recado = ("sem dados: depende do agente da Fase 3 nas Rasps"
                  if spec.phase == 3 else "sem dados nesta janela")
        ax.text(0.5, 0.5, recado, ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="#868e96")
        ax.set_xticks([])
        ax.set_yticks([])
    return _png(fig)


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def render_metric_chart(store: Store, origin: str, spec: MetricSpec,
                        now: float, window_s: float, floor_s: float) -> bytes:
    if spec.kind == "outages":
        return _render_outages(store, origin, now, window_s, floor_s)
    return _render_series(store, origin, spec, now, window_s)
