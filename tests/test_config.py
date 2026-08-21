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


def test_charts_tem_padrao_e_le_do_exemplo():
    cfg = load_config(Path("config.example.toml"))
    assert cfg.charts.outage_min_s == 0          # 0 = piso automatico pela janela


def test_config_sem_secao_charts_ainda_carrega(tmp_path):
    """O config.toml que ja roda no PC do SPA nao tem [charts]. Se load_config
    exigir a secao, o monitor nao sobe no proximo restart."""
    base = Path("config.example.toml").read_text(encoding="utf-8")
    sem = "\n".join(l for l in base.splitlines()
                    if not l.startswith("[charts]")
                    and not l.startswith("outage_min_s"))
    alvo = tmp_path / "config.toml"
    alvo.write_text(sem, encoding="utf-8")

    assert load_config(alvo).charts.outage_min_s == 0
