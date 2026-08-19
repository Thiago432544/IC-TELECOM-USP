"""Resumo diario das 08:00."""
from __future__ import annotations
import time

from monitor.config import Config
from monitor.store import Store


def build_daily_summary(store: Store, cfg: Config, now: float) -> str:
    since = now - 86400
    lines = ["Resumo diario - cameras Porto de Santos", ""]
    for cam in sorted(cfg.cameras):
        n_disc = store.count_events(cam, "DISCONNECT", since)
        samples = store.samples(cam, "frames_min", since)
        if samples:
            avail = 100.0 * sum(1 for _, v in samples if v > 0) / len(samples)
            avail_s = f"{avail:.0f}% do dia com frames"
        else:
            avail_s = "sem dados"
        worst = _worst_hour(store, cam, since)
        worst_s = f", pior hora {worst}h" if worst is not None else ""
        lines.append(f"- {cam}: {n_disc} quedas, {avail_s}{worst_s}")
    disk = store.last_sample("pc", "disk_free_gb")
    if disk:
        trend = _disk_trend(store, now)
        t = f" ({trend:+.1f} GB/dia)" if trend is not None else ""
        lines += ["", f"Disco D: {disk[1]:.0f} GB livres{t}"]
    return "\n".join(lines)


def _worst_hour(store, cam, since):
    hours = [time.localtime(e[0]).tm_hour
             for e in store.events(since, kind="DISCONNECT", origin=cam)]
    return max(set(hours), key=hours.count) if hours else None


def _disk_trend(store, now):
    pts = store.samples("pc", "disk_free_gb", now - 7 * 86400)
    if len(pts) < 2:
        return None
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    days = (t1 - t0) / 86400
    return (v1 - v0) / days if days > 0.5 else None
