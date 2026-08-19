from monitor.summary import build_daily_summary
from monitor.config import load_config
from monitor.store import Store
from pathlib import Path

def test_summary_counts_disconnects_and_availability(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    for i in range(24 * 6):                       # amostra a cada 10 min
        s.add_sample(now - 86400 + i * 600, "102", "frames_min",
                     0.0 if i < 12 else 50.0)     # 2h fora do ar
    for i in range(7):
        s.add_event(now - 3600 * i, "106", "DISCONNECT", "timeout")
    s.add_sample(now, "pc", "disk_free_gb", 250.0)
    text = build_daily_summary(s, cfg, now)
    assert "106: 7 quedas" in text
    assert "102" in text and "%" in text
    assert "250" in text
    s.close()
