from pathlib import Path

from monitor.config import load_config
from monitor.store import Store
from monitor.summary import build_daily_summary

NOW = 1_700_000_000.0


def _saudavel(s, cam, horas):
    for i in range(int(horas * 360)):
        s.add_sample(NOW - horas * 3600 + i * 10, cam, "last_frame_age_s", 5.0)


def test_resumo_conta_quedas_como_o_grafico(tmp_path):
    """Terceiro lugar onde a contagem tinha que bater: /status, painel, resumo."""
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    _saudavel(s, "106", 4)
    for i in range(50):                       # ruido de reconexao
        s.add_event(NOW - 3000 + i * 30, "106", "DISCONNECT", "timeout")

    texto = build_daily_summary(s, cfg, NOW)

    assert "106: sem intervalo" in texto
    assert "50 intervalos" not in texto
    s.close()


def test_resumo_usa_a_mesma_disponibilidade_do_status(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    _saudavel(s, "102", 4)

    assert "imagem 100.0%" in build_daily_summary(s, cfg, NOW)
    s.close()


def test_resumo_diz_o_piso_no_cabecalho(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")

    assert ">=5min" in build_daily_summary(s, cfg, NOW)
    s.close()


def test_resumo_mantem_o_disco(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    s.add_sample(NOW, "pc", "disk_free_gb", 250.0)

    assert "250" in build_daily_summary(s, cfg, NOW)
    s.close()
