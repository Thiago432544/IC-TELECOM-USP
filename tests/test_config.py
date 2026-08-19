from pathlib import Path
from monitor.config import load_config

def test_load_example_config():
    cfg = load_config(Path("config.example.toml"))
    assert cfg.cameras["102"].save_every == 10
    assert cfg.cameras["105"].save_every == 1
    assert cfg.paths.images == Path(r"D:\SPA_Data\Imagens_Porto")
    assert cfg.telegram.enabled is False
    assert cfg.alerts.down_after_s == 180
    assert cfg.alerts.realert_s == 1800
    assert cfg.panel.port == 8080
    assert cfg.summary_hour == 8
