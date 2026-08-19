import os
import time
from monitor.watcher import FrameWatcher

def _day(now):
    return time.strftime("%Y_%m_%d", time.localtime(now))

def _mkframe(root, day, cam, name, mtime):
    d = root / day / cam
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"x")
    os.utime(p, (mtime, mtime))

def test_first_poll_has_no_rate_but_has_age(tmp_path):
    now = time.time()
    _mkframe(tmp_path, _day(now), "102", "a.jpg", now - 30)
    w = FrameWatcher(tmp_path, {"102": 10})
    [s] = w.poll(now)
    assert s.frames_per_min is None
    assert 25 <= s.last_frame_age_s <= 35

def test_rate_corrected_by_save_every(tmp_path):
    now = time.time()
    w = FrameWatcher(tmp_path, {"102": 10})
    w.poll(now - 60)                          # abre a janela
    for i in range(6):                        # 6 gravados em 60s
        _mkframe(tmp_path, _day(now), "102", f"f{i}.jpg", now - 50 + i * 8)
    [s] = w.poll(now)
    # 6 arquivos * save_every 10 / 1 min = 60 frames/min
    assert abs(s.frames_per_min - 60.0) < 1.0

def test_missing_today_falls_back_to_yesterday(tmp_path):
    now = time.time()
    yesterday = now - 86400
    _mkframe(tmp_path, _day(yesterday), "106", "old.jpg", yesterday)
    w = FrameWatcher(tmp_path, {"106": 10})
    w.poll(now - 60)
    [s] = w.poll(now)
    assert s.frames_per_min == 0.0
    assert s.last_frame_age_s >= 86000

def test_never_any_frame(tmp_path):
    now = time.time()
    w = FrameWatcher(tmp_path, {"105": 1})
    w.poll(now - 60)
    [s] = w.poll(now)
    assert s.frames_per_min == 0.0 and s.last_frame_age_s is None
