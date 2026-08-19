from datetime import datetime
from monitor.baseline import hourly_baseline
from monitor.store import Store

def _at(day, hour):
    return datetime(2026, 8, day, hour, 30).timestamp()

def test_median_of_matching_hour_only(tmp_path):
    s = Store(tmp_path / "m.db")
    for day in range(10, 17):                       # 7 dias
        for i, v in enumerate([50.0, 55.0, 60.0]):  # 3 amostras na hora 14
            s.add_sample(_at(day, 14) + i * 60, "102", "frames_min", v)
        s.add_sample(_at(day, 3), "102", "frames_min", 5.0)  # madrugada != hora 14
    now = _at(17, 15)
    assert hourly_baseline(s, "102", 14, now) == 55.0
    s.close()

def test_none_when_insufficient(tmp_path):
    s = Store(tmp_path / "m.db")
    for i in range(5):
        s.add_sample(_at(16, 14) + i * 60, "102", "frames_min", 50.0)
    assert hourly_baseline(s, "102", 14, _at(17, 15)) is None
    s.close()
