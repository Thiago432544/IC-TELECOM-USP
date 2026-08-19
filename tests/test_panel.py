from pathlib import Path
from monitor.config import load_config
from monitor.panel import build_status, render_html
from monitor.store import Store

def test_build_status_states(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    s.add_sample(now - 30, "102", "frames_min", 55.0)
    s.add_sample(now - 30, "102", "last_frame_age_s", 10.0)
    s.add_sample(now - 30, "106", "last_frame_age_s", 400.0)
    st = build_status(s, cfg, now)
    assert st["cameras"]["102"]["state"] == "ok"
    assert st["cameras"]["106"]["state"] == "atrasada"
    assert st["cameras"]["105"]["state"] == "sem_dados"
    s.close()

def test_render_html_contains_cameras(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    html = render_html(build_status(s, cfg, 1_700_000_000.0))
    assert "102" in html and "105" in html and "106" in html
    assert 'http-equiv="refresh"' in html
    s.close()
