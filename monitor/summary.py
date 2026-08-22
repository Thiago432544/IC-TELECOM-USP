"""Resumo diario das 08:00."""
from __future__ import annotations
import time

from monitor.config import Config
from monitor.metrics import DEFAULT_WINDOW, label_duration, outage_floor
from monitor.store import Store
from monitor.uptime import availability, coverage_gaps, outages


def build_daily_summary(store: Store, cfg: Config, now: float) -> str:
    """Mesma contagem do /grafico e do /status - tres textos, um numero.

    Contar DISCONNECT cru aqui daria "106: 276 quedas" enquanto o grafico da
    mesma camera diria "3 quedas": os dois certos, medindo coisas diferentes,
    e juntos destruindo a confianca nos dois.
    """
    piso = outage_floor(DEFAULT_WINDOW, cfg.charts.outage_min_s or None)
    since = now - DEFAULT_WINDOW
    lines = [f"Resumo diario - cameras Porto de Santos "
             f"({label_duration(DEFAULT_WINDOW)}, intervalos sem imagem "
             f">={label_duration(piso)})",
             ""]
    for cam in sorted(cfg.cameras):
        outs = outages(store, cam, since, now, piso)
        gaps = coverage_gaps(store, cam, since, now)
        avail = availability(outs, since, now, tuple(gaps))
        n = len(outs)
        gaps = ("sem intervalo" if n == 0
                else f"{n} intervalo{'s' if n > 1 else ''} sem imagem")
        img = "sem dados" if avail is None else f"imagem {avail}%"
        pior = _pior_hora(outs)
        pior_s = f", pior hora {pior}h" if pior is not None else ""
        lines.append(f"- {cam}: {gaps}, {img}{pior_s}")
    disk = store.last_sample("pc", "disk_free_gb")
    if disk:
        trend = _disk_trend(store, now)
        t = f" ({trend:+.1f} GB/dia)" if trend is not None else ""
        lines += ["", f"Disco D: {disk[1]:.0f} GB livres{t}"]
    return "\n".join(lines)


def _pior_hora(outs):
    """Hora que concentrou mais intervalos sem imagem - candidata a mare."""
    horas = [time.localtime(o.start).tm_hour for o in outs]
    return max(set(horas), key=horas.count) if horas else None


def _disk_trend(store, now):
    pts = store.samples("pc", "disk_free_gb", now - 7 * 86400)
    if len(pts) < 2:
        return None
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    days = (t1 - t0) / 86400
    return (v1 - v0) / days if days > 0.5 else None
