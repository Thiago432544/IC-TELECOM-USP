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


def _serie(store, cam, t0, n, idade=5.0, passo=10.0):
    for i in range(n):
        store.add_sample(t0 + i * passo, cam, "last_frame_age_s", float(idade))


def test_status_conta_quedas_como_o_grafico_e_nao_o_log(tmp_path):
    """276 DISCONNECT no log e 3 quedas no grafico sao a mesma camera medindo
    coisas diferentes. Lado a lado, confunde."""
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    _serie(s, "106", now - 3600, 360)              # 1h saudavel
    for i in range(50):                            # ruido: 50 reconexoes curtas
        s.add_event(now - 3000 + i * 30, "106", "DISCONNECT", "Timeout")

    st = build_status(s, cfg, now)["cameras"]["106"]

    assert st["outages_24h"] == 0                  # nenhuma passou de 5min
    assert st["disconnects_24h"] == 50             # o cru continua na API
    s.close()


def test_status_traz_disponibilidade(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    _serie(s, "105", now - 3600, 360)

    assert build_status(s, cfg, now)["cameras"]["105"]["uptime_24h"] == 100.0
    s.close()


def test_status_sem_dados_nao_inventa_disponibilidade(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")

    st = build_status(s, cfg, 1_700_000_000.0)["cameras"]["105"]

    assert st["uptime_24h"] is None
    s.close()


def test_status_expoe_o_piso_usado(tmp_path):
    """O piso muda a contagem, entao nao pode ficar implicito."""
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")

    assert build_status(s, cfg, 1_700_000_000.0)["outage_floor_s"] == 300
    s.close()


def test_html_mostra_disponibilidade_no_lugar_do_disconnect_cru(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = 1_700_000_000.0
    _serie(s, "102", now - 3600, 360)

    html = render_html(build_status(s, cfg, now))

    assert "Imagem 24h" in html
    assert "&ge;5min" in html
