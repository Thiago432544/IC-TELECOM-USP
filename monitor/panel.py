"""Painel local somente leitura: / (HTML, refresh 30s) e /api/status (JSON)."""
from __future__ import annotations
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from monitor.config import Config
from monitor.store import Store


def build_status(store: Store, cfg: Config, now: float) -> dict:
    cams = {}
    for cam in cfg.cameras:
        fpm = store.last_sample(cam, "frames_min")
        age = store.last_sample(cam, "last_frame_age_s")
        if age is None:
            state = "sem_dados"
        elif age[1] > 180:
            state = "atrasada"
        else:
            state = "ok"
        cams[cam] = {
            "state": state,
            "frames_min": fpm[1] if fpm else None,
            "last_frame_age_s": age[1] if age else None,
            "disconnects_24h": store.count_events(cam, "DISCONNECT", now - 86400),
        }
    disk = store.last_sample("pc", "disk_free_gb")
    rsrp = store.last_sample("cpe", "rsrp")
    up = store.last_sample("cpe", "cpe_up")
    return {"cameras": cams,
            "disk_free_gb": disk[1] if disk else None,
            "rsrp": rsrp[1] if rsrp else None,
            "cpe_up": bool(up[1]) if up else None}


_COLORS = {"ok": "#2f9e44", "atrasada": "#e8590c", "sem_dados": "#868e96"}


def render_html(status: dict) -> str:
    rows = []
    for cam, c in sorted(status["cameras"].items()):
        color = _COLORS[c["state"]]
        fpm = f'{c["frames_min"]:.1f}' if c["frames_min"] is not None else "-"
        age = f'{c["last_frame_age_s"]:.0f}s' if c["last_frame_age_s"] is not None else "-"
        rows.append(
            f'<tr><td><b>{cam}</b></td>'
            f'<td style="color:{color};font-weight:600">{c["state"]}</td>'
            f'<td>{fpm}</td><td>{age}</td><td>{c["disconnects_24h"]}</td></tr>')
    disk = (f'{status["disk_free_gb"]:.0f} GB'
            if status["disk_free_gb"] is not None else "-")
    rsrp = f'{status["rsrp"]:.0f} dBm' if status["rsrp"] is not None else "-"
    stamp = time.strftime("%d/%m/%Y %H:%M:%S")
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30"><title>Cameras Porto de Santos</title>
<style>body{{font-family:Segoe UI,sans-serif;margin:2rem;background:#f8f9fa}}
table{{border-collapse:collapse}}td,th{{padding:.5rem 1rem;border-bottom:1px solid #dee2e6;text-align:left}}</style>
</head><body><h1>Cameras Porto de Santos</h1>
<table><tr><th>Camera</th><th>Estado</th><th>frames/min</th><th>Ultimo frame</th><th>Quedas 24h</th></tr>
{''.join(rows)}</table>
<p>Disco D: {disk} &middot; RSRP: {rsrp} &middot; atualizado {stamp} (recarrega a cada 30s)</p>
</body></html>"""


def run_panel(store: Store, cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            now = time.time()
            if self.path == "/api/status":
                body = json.dumps(build_status(store, cfg, now)).encode()
                ctype = "application/json"
            else:
                body = render_html(build_status(store, cfg, now)).encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("0.0.0.0", cfg.panel.port), Handler).serve_forever()
