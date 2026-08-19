"""Comandos do bot: /status e /grafico <id>."""
from __future__ import annotations
from typing import Optional

from monitor.charts import render_camera_chart
from monitor.config import Config
from monitor.panel import build_status
from monitor.store import Store

_HELP = ("Comandos:\n/status - estado atual das cameras\n"
         "/grafico <id> - ultimas 24h da camera (ex.: /grafico 102)")


class BotHandler:
    def __init__(self, store: Store, cfg: Config):
        self.store = store
        self.cfg = cfg

    def handle(self, text: str, now: float) -> tuple[str, Optional[bytes]]:
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""
        if cmd == "/status":
            return self._status(now), None
        if cmd == "/grafico" and len(parts) > 1 and parts[1] in self.cfg.cameras:
            png = render_camera_chart(self.store, parts[1], now, window_s=86400)
            return f"Camera {parts[1]} - ultimas 24h", png
        return _HELP, None

    def _status(self, now: float) -> str:
        st = build_status(self.store, self.cfg, now)
        icon = {"ok": "OK", "atrasada": "ATRASADA", "sem_dados": "SEM DADOS"}
        lines = ["Estado atual:"]
        for cam, c in sorted(st["cameras"].items()):
            fpm = f'{c["frames_min"]:.1f} f/min' if c["frames_min"] is not None else "-"
            age = (f'{c["last_frame_age_s"]:.0f}s atras'
                   if c["last_frame_age_s"] is not None else "-")
            lines.append(f'- {cam}: {icon[c["state"]]} | {fpm} | ultimo frame {age} '
                         f'| {c["disconnects_24h"]} quedas 24h')
        if st["disk_free_gb"] is not None:
            lines.append(f'Disco D: {st["disk_free_gb"]:.0f} GB livres')
        if st["rsrp"] is not None:
            lines.append(f'Enlace: RSRP {st["rsrp"]:.0f} dBm')
        return "\n".join(lines)
