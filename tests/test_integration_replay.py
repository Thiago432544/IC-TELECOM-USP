"""Replay: log de conexoes + amostras sinteticas -> alertas esperados."""
import time
from pathlib import Path
from monitor.alerts import AlertEngine
from monitor.config import load_config
from monitor.service import build_snapshot
from monitor.store import Store
from monitor.taplog import LogFollower

def test_flapping_then_down_then_recovery(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    store = Store(tmp_path / "m.db")
    engine = AlertEngine(cfg.alerts)
    t0 = time.time()

    # 1) flapping: 6 quedas da 106 em 10 min, registradas via taplog
    log = tmp_path / "log.txt"
    lines = []
    for i in range(6):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0 - 600 + i * 100))
        lines.append(f"{stamp} | DISCONNECT | client=106 | [106] Timeout | total=1")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    f = LogFollower(log, on_event=lambda ev: store.add_event(
        ev.ts, ev.client or "server", ev.kind, ev.detail))
    assert f.poll() == 6

    # cameras com frames ok
    for cam in ("102", "105", "106"):
        store.add_sample(t0, cam, "frames_min", 50.0)
        store.add_sample(t0, cam, "last_frame_age_s", 10.0)

    out = engine.evaluate(t0, build_snapshot(store, cfg, t0, disk_free_gb=200.0))
    assert [(a.kind, a.origin) for a in out] == [("flapping", "106")]

    # 2) down: a 102 para de entregar
    t1 = t0 + 60
    store.add_sample(t1, "102", "last_frame_age_s", 300.0)
    out = engine.evaluate(t1, build_snapshot(store, cfg, t1, disk_free_gb=200.0))
    assert ("down", "102") in [(a.kind, a.origin) for a in out]

    # 3) recovery
    t2 = t1 + 300
    store.add_sample(t2, "102", "last_frame_age_s", 5.0)
    out = engine.evaluate(t2, build_snapshot(store, cfg, t2, disk_free_gb=200.0))
    assert [(a.kind, a.origin) for a in out] == [("recovered", "102")]
    store.close()
