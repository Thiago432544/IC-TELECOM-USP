from pathlib import Path
from monitor.bot import BotHandler
from monitor.config import load_config
from monitor.store import Store

def _mk(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    s.add_sample(now - 30, "102", "frames_min", 55.0)
    return BotHandler(s, cfg), now, s

def test_status_lists_cameras(tmp_path):
    bot, now, s = _mk(tmp_path)
    text, png = bot.handle("/status", now)
    assert png is None
    for cam in ("102", "105", "106"):
        assert cam in text
    s.close()

def test_grafico_returns_png(tmp_path):
    bot, now, s = _mk(tmp_path)
    text, png = bot.handle("/grafico 102", now)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    s.close()

def test_unknown_command_gets_help(tmp_path):
    bot, now, s = _mk(tmp_path)
    text, png = bot.handle("/xyz", now)
    assert "/status" in text and png is None
    s.close()
