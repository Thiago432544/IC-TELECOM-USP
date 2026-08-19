import time
from pathlib import Path
from monitor.config import load_config
from monitor.service import build_snapshot, should_send_summary
from monitor.store import Store

def test_build_snapshot_reads_store(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = time.time()
    s.add_sample(now - 5, "102", "frames_min", 50.0)
    s.add_sample(now - 5, "102", "last_frame_age_s", 12.0)
    for i in range(5):
        s.add_event(now - 60 * i, "102", "DISCONNECT", "x")
    snap = build_snapshot(s, cfg, now, disk_free_gb=100.0)
    c = snap.cameras["102"]
    assert c.frames_per_min == 50.0 and c.last_frame_age_s == 12.0
    assert c.disconnects_15min == 5
    assert snap.cameras["105"].last_frame_age_s is None
    assert snap.disk_free_gb == 100.0
    s.close()

def test_should_send_summary_once_per_day():
    ts_0830 = time.mktime((2026, 8, 19, 8, 30, 0, 0, 0, -1))
    ts_0730 = time.mktime((2026, 8, 19, 7, 30, 0, 0, 0, -1))
    assert should_send_summary(None, ts_0830, 8) is True
    assert should_send_summary("2026-08-19", ts_0830, 8) is False
    assert should_send_summary("2026-08-18", ts_0730, 8) is False   # antes das 8
    assert should_send_summary("2026-08-18", ts_0830, 8) is True
