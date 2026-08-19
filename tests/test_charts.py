from monitor.charts import render_camera_chart
from monitor.store import Store

def test_renders_png_with_data_and_events(tmp_path):
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    for i in range(30):
        s.add_sample(now - 1800 + i * 60, "106", "frames_min", 30.0 + i)
        s.add_sample(now - 1800 + i * 60, "cpe", "rsrp", -95.0)
    s.add_event(now - 900, "106", "DISCONNECT", "timeout")
    png = render_camera_chart(s, "106", now)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    s.close()

def test_renders_even_with_empty_store(tmp_path):
    s = Store(tmp_path / "m.db")
    png = render_camera_chart(s, "102", 1_700_000_000.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    s.close()
