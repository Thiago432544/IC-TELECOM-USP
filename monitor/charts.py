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
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from monitor.metrics import MetricSpec, label_duration
from monitor.store import Store
from monitor.uptime import (Outage, availability, coverage_gaps,
                            disconnect_times, outages)

DIA = 86400

COR_UP = "#2f9e44"
COR_DOWN = "#e03131"
COR_UNK = "#ced4da"
COR_DISC = "#1864ab"


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
            outs: list[Outage], avail, n_disc=None) -> str:
    """Intervalo sem imagem e desconexao sao numeros diferentes, lado a lado.

    buracos >> desconexoes = enlace estrangulado, entregando devagar.
    buracos ~= desconexoes = enlace caindo.
    """
    piso = label_duration(floor_s)
    partes = [f"{camera} · {label_duration(window_s)}",
              "sem dados" if avail is None else f"imagem {avail}%"]
    if outs:
        n = len(outs)
        partes.append(f"{n} intervalo{'s' if n > 1 else ''} >={piso}")
    else:
        partes.append(f"sem intervalo >={piso}")
    if n_disc is not None:
        partes.append(f"{n_disc} desconexao" if n_disc == 1
                      else f"{n_disc} desconexoes")
    if outs:
        partes.append(f"maior {fmt_dur(max(o.duration_s for o in outs))}")
    return "  ·  ".join(partes)


def lane_mode(n_disc: int, max_ticks: int = 60) -> str:
    """Marca individual enquanto da para separar; acima disso, densidade.

    150 marcas numa faixa de 24h viram uma tarja preta que nao informa nada.
    """
    return "ticks" if n_disc <= max_ticks else "densidade"


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


def _pista_desconexao(ax, discs, since, until, y, altura):
    """Desconexao e' evento pontual, entao vive numa pista propria embaixo da
    faixa - sobreposta a ela, brigaria com o vermelho do intervalo."""
    if not discs:
        return
    xs = [d - since for d in discs]
    if lane_mode(len(discs)) == "ticks":
        ax.vlines(xs, y, y + altura, color=COR_DISC, lw=1.4)
        return
    # densidade: conta por balde e modula a opacidade, para nunca virar tarja
    n_bins = 160
    largura = (until - since) / n_bins
    contagem = [0] * n_bins
    for x in xs:
        contagem[min(n_bins - 1, int(x / largura))] += 1
    pico = max(contagem)
    for i, c in enumerate(contagem):
        if c:
            ax.broken_barh([(i * largura, largura)], (y, altura),
                           facecolors=COR_DISC, alpha=0.25 + 0.75 * c / pico)


def build_outage_figure(store: Store, camera: str, now: float,
                        window_s: float, floor_s: float):
    since, until = now - window_s, now
    outs = outages(store, camera, since, until, floor_s)
    gaps = coverage_gaps(store, camera, since, until)
    discs = disconnect_times(store, camera, since, until)
    avail = availability(outs, since, until, tuple(gaps))
    rows = band_rows(since, until)
    calendario = len(rows) > 1
    alvo = [(o.start, o.end if o.end is not None else until) for o in outs]

    fig, ax = plt.subplots(figsize=(9, 2.0 + 0.42 * len(rows)), dpi=110)
    altura = 0.6
    for i, r in enumerate(rows):
        y = len(rows) - 1 - i
        ax.broken_barh(_spans_offset([(since, until)], r, since, until),
                       (y - altura / 2, altura), facecolors=COR_UP)
        ax.broken_barh(_spans_offset(gaps, r, since, until),
                       (y - altura / 2, altura), facecolors=COR_UNK)
        ax.broken_barh(_spans_offset(alvo, r, since, until),
                       (y - altura / 2, altura), facecolors=COR_DOWN)

    if calendario:
        # Marca individual nao cabe em varios dias; o numero do dia cabe.
        ax.set_ylim(-0.6, len(rows) - 0.4)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r.label for r in reversed(rows)])
        eixo_dir = ax.twinx()
        eixo_dir.set_ylim(ax.get_ylim())
        eixo_dir.set_yticks(range(len(rows)))
        eixo_dir.set_yticklabels(
            [str(sum(1 for d in discs if r.start <= d < r.end))
             for r in reversed(rows)], fontsize=9)
        eixo_dir.set_ylabel("desconexoes no dia", fontsize=9)
        for lado in ("top", "right", "left"):
            eixo_dir.spines[lado].set_visible(False)
        eixo_dir.tick_params(axis="y", length=0)
    else:
        pista_y, pista_h = -0.62, 0.16
        _pista_desconexao(ax, discs, since, until, pista_y, pista_h)
        ax.set_ylim(pista_y - 0.18, 0.5)
        ax.set_yticks([0, pista_y + pista_h / 2])
        ax.set_yticklabels(["imagem", "desconexao"], fontsize=9)

    ax.tick_params(axis="y", length=0)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    _eixo_x(ax, since, until, calendario)
    ax.set_title("\n".join([f"Camera {camera} - imagem",
                            caption(camera, window_s, floor_s, outs, avail,
                                    len(discs))]),
                 fontsize=10)
    fig.legend(handles=[Patch(facecolor=COR_UP, label="imagem"),
                        Patch(facecolor=COR_DOWN, label="sem imagem"),
                        Patch(facecolor=COR_UNK, label="sem dados"),
                        Patch(facecolor=COR_DISC, label="desconexao")],
               loc="lower center", ncol=4, frameon=False, fontsize=9)
    fig.subplots_adjust(bottom=0.30 if len(rows) == 1 else 0.14)
    return fig


def _render_outages(store: Store, camera: str, now: float,
                    window_s: float, floor_s: float) -> bytes:
    return _png(build_outage_figure(store, camera, now, window_s, floor_s))


def series_title(origin: str, spec: MetricSpec, window_s: float) -> str:
    """Metrica do CPE ou do PC nao leva o numero da camera no titulo.

    "106 - RSRP" faria parecer o radio da 106; o cpe.py le o CPE do lado do
    SPA. Confundir os dois e' o erro que o diario de 20/08 registra.
    """
    quem = origin if spec.origin == "camera" else spec.origin
    return f"{quem} - {spec.label} - {label_duration(window_s)}"


def _render_series(store: Store, origin: str, spec: MetricSpec,
                   now: float, window_s: float,
                   nota: Optional[str] = None) -> bytes:
    since = now - window_s
    alvo = origin if spec.origin == "camera" else spec.origin
    rows = store.samples(alvo, spec.sample, since, now)

    fig, ax = plt.subplots(figsize=(9, 4), dpi=110)
    ax.set_title(series_title(origin, spec, window_s), fontsize=10)
    # o titulo ja diz a metrica por extenso; repeti-la no eixo so rouba espaco
    ax.set_ylabel(spec.unit)
    if rows:
        ax.plot([datetime.fromtimestamp(t) for t, _ in rows],
                [v for _, v in rows], lw=1.6)
        fig.autofmt_xdate()
    else:
        recado = nota or ("sem dados: depende do agente da Fase 3 nas Rasps"
                          if spec.phase == 3 else "sem dados nesta janela")
        ax.text(0.5, 0.5, _quebra(recado, 52), ha="center",
                va="center", transform=ax.transAxes, fontsize=11,
                color="#868e96")
        ax.set_xticks([])
        ax.set_yticks([])
    return _png(fig)


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _quebra(texto: str, largura: int) -> str:
    linhas, atual = [], ""
    for palavra in texto.split():
        if atual and len(atual) + 1 + len(palavra) > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = f"{atual} {palavra}".strip()
    if atual:
        linhas.append(atual)
    return "\n".join(linhas)


def render_metric_chart(store: Store, origin: str, spec: MetricSpec,
                        now: float, window_s: float, floor_s: float,
                        nota: Optional[str] = None) -> bytes:
    if spec.kind == "outages":
        return _render_outages(store, origin, now, window_s, floor_s)
    return _render_series(store, origin, spec, now, window_s, nota)
