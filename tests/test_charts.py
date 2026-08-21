"""Grafico de conexao: layout por janela, legenda e render."""
from datetime import datetime, timedelta

import pytest

from monitor.charts import band_rows, caption, fmt_dur, render_metric_chart
from monitor.metrics import find_metric
from monitor.store import Store
from monitor.uptime import Outage

PNG = b"\x89PNG\r\n\x1a\n"


def _meio_dia(ano=2026, mes=8, dia=20):
    return datetime(ano, mes, dia, 12, 0, 0).timestamp()


@pytest.mark.parametrize("seg,texto", [
    (45, "45s"), (120, "2min"), (2820, "47min"),
    (3600, "1h"), (19260, "5h21"), (86400, "1d"), (105000, "1d5h"),
])
def test_duracao_sai_legivel(seg, texto):
    assert fmt_dur(seg) == texto


def test_ate_24h_o_grafico_e_uma_faixa_so():
    t = _meio_dia()
    assert len(band_rows(t - 7200, t)) == 1
    assert len(band_rows(t - 86400, t)) == 1


def test_acima_de_24h_vira_uma_linha_por_dia():
    """Faixa de 7 dias esticada numa linha so e' ilegivel; empilhada por dia
    da para bater o mesmo horario de um dia contra o outro."""
    fim = _meio_dia(2026, 8, 20)
    rows = band_rows(fim - 7 * 86400, fim)

    assert len(rows) == 8                      # 7 dias corridos tocam 8 datas
    assert [r.label for r in rows] == [
        (datetime(2026, 8, 13) + timedelta(days=i)).strftime("%d/%m")
        for i in range(8)]


def test_cada_linha_do_calendario_cobre_um_dia_local():
    fim = _meio_dia(2026, 8, 20)
    rows = band_rows(fim - 2 * 86400, fim)
    for r in rows:
        assert r.end - r.start == 86400
        assert datetime.fromtimestamp(r.start).hour == 0


def test_legenda_diz_disponibilidade_quantidade_e_piso():
    """O piso tem que aparecer sempre: numero que muda o que voce ve nao pode
    ficar implicito."""
    t = _meio_dia()
    outs = [Outage(t - 20000, t - 740, 19260, "Timeout"),
            Outage(t - 500, t - 200, 300, "Timeout")]
    txt = caption("106", 86400, 300, outs, 91.4)

    assert "106" in txt and "24h" in txt
    assert "91.4%" in txt
    assert "2 quedas" in txt
    assert ">=5min" in txt
    assert "5h21" in txt          # a maior


def test_legenda_sem_queda_nao_inventa_maior():
    txt = caption("105", 86400, 300, [], 100.0)
    assert "sem queda" in txt and "maior" not in txt


def test_render_conexao_devolve_png(tmp_path):
    s = Store(tmp_path / "m.db")
    t = _meio_dia()
    for i in range(360):                       # 1h de amostras de 10 em 10s
        idade = 5.0 if i < 100 or i > 200 else (i - 100) * 10.0
        s.add_sample(t - 3600 + i * 10, "106", "last_frame_age_s", idade)
    s.add_event(t - 2600, "106", "DISCONNECT", "Timeout")

    png = render_metric_chart(s, "106", find_metric("conexao"), t, 3600, 30)
    assert png[:8] == PNG
    s.close()


def test_render_conexao_em_calendario_devolve_png(tmp_path):
    s = Store(tmp_path / "m.db")
    t = _meio_dia()
    for i in range(0, 7 * 86400, 300):
        s.add_sample(t - 7 * 86400 + i, "106", "last_frame_age_s", 5.0)

    png = render_metric_chart(s, "106", find_metric("conexao"), t,
                              7 * 86400, 1800)
    assert png[:8] == PNG
    s.close()


def test_render_serie_devolve_png(tmp_path):
    s = Store(tmp_path / "m.db")
    t = _meio_dia()
    for i in range(60):
        s.add_sample(t - 3600 + i * 60, "102", "frames_min", 55.0 + i % 5)

    png = render_metric_chart(s, "102", find_metric("frames"), t, 3600, 30)
    assert png[:8] == PNG
    s.close()


def test_render_de_metrica_sem_dados_ainda_devolve_png(tmp_path):
    """Fase 3 nao subiu: /grafico 106 memoria nao pode estourar."""
    s = Store(tmp_path / "m.db")
    png = render_metric_chart(s, "106", find_metric("memoria"),
                              _meio_dia(), 86400, 300)
    assert png[:8] == PNG
    s.close()


def test_render_com_store_vazia_devolve_png(tmp_path):
    s = Store(tmp_path / "m.db")
    png = render_metric_chart(s, "102", find_metric("conexao"),
                              _meio_dia(), 86400, 300)
    assert png[:8] == PNG
    s.close()


def test_legenda_sem_cobertura_nao_inventa_porcentagem():
    txt = caption("106", 86400, 300, [], None)
    assert "sem dados" in txt and "%" not in txt
