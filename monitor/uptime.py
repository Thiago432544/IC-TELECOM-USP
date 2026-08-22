"""Reconstroi quedas reais a partir da serie de idade do ultimo frame.

O log de CONNECT/DISCONNECT nao serve sozinho: some quando o servidor congela
(F11) e nao enxerga camera conectada porem muda. A idade do ultimo frame ve os
dois casos, e como a propria amostra carrega a idade, a borda da queda sai com
precisao de segundo mesmo amostrando de 10 em 10. Os DISCONNECT entram apenas
para rotular o motivo.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from monitor.store import Store

GAP_S = 120.0        # sem amostra por mais que isso: o monitor e' que estava fora


@dataclass(frozen=True)
class Outage:
    start: float
    end: Optional[float]          # None = ainda fora no fim da janela
    duration_s: float
    reason: Optional[str] = None


def _serie(store: Store, camera: str, since: float, until: float):
    return store.samples(camera, "last_frame_age_s", since, until)


def coverage_gaps(store: Store, camera: str, since: float, until: float,
                  gap_s: float = GAP_S) -> list[tuple[float, float]]:
    """Trechos sem amostra: o monitor estava fora e nao sabemos o estado.

    Nunca vira "no ar" - ignorancia pintada de verde e' o pior erro possivel
    num grafico de disponibilidade.
    """
    rows = _serie(store, camera, since, until)
    if not rows:
        return [(since, until)]
    out, prev = [], None
    if rows[0][0] - since > gap_s:
        out.append((since, rows[0][0]))      # antes da primeira amostra
    for ts, _ in rows:
        if prev is not None and ts - prev > gap_s:
            out.append((prev, ts))
        prev = ts
    if until - prev > gap_s:
        out.append((prev, until))            # monitor caiu e nao voltou
    return out


def _motivo(store: Store, camera: str, start: float, fim: float) -> Optional[str]:
    motivos = [d for ts, _, _, d in store.events(start, kind="DISCONNECT",
                                                 origin=camera)
               if start <= ts <= fim]
    return motivos[-1] if motivos else None


def outages(store: Store, camera: str, since: float, until: float,
            min_s: float, gap_s: float = GAP_S) -> list[Outage]:
    """Quedas com duracao >= min_s dentro de [since, until].

    min_s e' o unico botao: com piso alto sobram as panes, com piso baixo
    aparece o flap. Quem escolhe o piso e' quem escolhe a janela.
    """
    spans: list[tuple[float, Optional[float]]] = []
    start: Optional[float] = None
    prev_ts: Optional[float] = None
    for ts, age in _serie(store, camera, since, until):
        if start is not None and prev_ts is not None and ts - prev_ts > gap_s:
            spans.append((start, prev_ts))     # fecha no ultimo instante conhecido
            start = None
        if age >= min_s:
            if start is None:
                start = ts - age               # instante real do ultimo frame
        elif start is not None:
            spans.append((start, ts - age))    # instante real do frame que voltou
            start = None
        prev_ts = ts
    if start is not None:
        spans.append((start, None))

    out = []
    for a, b in spans:
        dur = (b if b is not None else until) - a
        if dur >= min_s:
            out.append(Outage(a, b, dur, _motivo(store, camera, a,
                                                 b if b is not None else until)))
    return out


def availability(outs: list[Outage], since: float, until: float,
                 gaps: tuple = ()) -> Optional[float]:
    """Porcentagem do tempo *conhecido* em que a camera estava entregando.

    O tempo sem cobertura sai do denominador: nao entra como no ar nem como
    queda.
    """
    conhecido = (until - since) - sum(min(b, until) - max(a, since)
                                      for a, b in gaps
                                      if min(b, until) > max(a, since))
    if conhecido <= 0:
        return None                          # nao sei; 100% e 0% seriam mentira
    fora = 0.0
    for o in outs:
        a = max(o.start, since)
        b = min(o.end if o.end is not None else until, until)
        if b > a:
            fora += b - a
    return round(100.0 * (1.0 - fora / conhecido), 1)


def disconnect_times(store: Store, camera: str, since: float,
                     until: float) -> list[float]:
    """Instantes de DISCONNECT na janela.

    Existe separado de outages() de proposito: desconexao e' evento, queda de
    imagem e' duracao. Confundir os dois faz "36 intervalos" parecer "36 vezes
    que o enlace caiu" - o que este projeto ja provou ser falso.
    """
    return [ts for ts, _, _, _ in store.events(since, kind="DISCONNECT",
                                               origin=camera)
            if ts <= until]
