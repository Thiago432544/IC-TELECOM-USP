"""Graficos PNG para os alertas e o comando /grafico."""
from __future__ import annotations
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from monitor.store import Store


def render_camera_chart(store: Store, camera: str, now: float,
                        window_s: int = 1800) -> bytes:
    since = now - window_s
    frames = store.samples(camera, "frames_min", since)
    rsrp = store.samples("cpe", "rsrp", since)
    disc = [e[0] for e in store.events(since, kind="DISCONNECT", origin=camera)]

    fig, ax1 = plt.subplots(figsize=(8, 4), dpi=110)
    title_win = window_s // 60
    ax1.set_title(f"Camera {camera} - ultimos {title_win} min")
    if frames:
        xs = [datetime.fromtimestamp(t) for t, _ in frames]
        ax1.plot(xs, [v for _, v in frames], lw=1.8, label="frames/min")
    ax1.set_ylabel("frames/min")
    ax1.set_ylim(bottom=0)
    for d in disc:
        ax1.axvline(datetime.fromtimestamp(d), ls="--", lw=0.8, alpha=0.6, color="red")
    if rsrp:
        ax2 = ax1.twinx()
        xs = [datetime.fromtimestamp(t) for t, _ in rsrp]
        ax2.plot(xs, [v for _, v in rsrp], lw=1.2, alpha=0.7, color="gray",
                 label="RSRP (dBm)")
        ax2.set_ylabel("RSRP (dBm)")
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
