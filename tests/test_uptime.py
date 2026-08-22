"""Reconstrucao de quedas a partir da serie de idade do ultimo frame."""
from monitor.store import Store
from monitor.uptime import availability, coverage_gaps, outages


def _serie(store, cam, t0, idades, passo=10.0):
    """Grava a serie de last_frame_age_s como o watcher gravaria."""
    for i, idade in enumerate(idades):
        store.add_sample(t0 + i * passo, cam, "last_frame_age_s", float(idade))


def test_queda_comeca_no_instante_exato_do_ultimo_frame(tmp_path):
    """A idade da amostra revela quando o frame chegou, entao a borda da queda
    e' precisa mesmo com amostragem de 10 em 10 segundos."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    # 0..90s saudavel; ultimo frame em t0+95; sem frame novo ate t0+398.
    idades = [5.0] * 10
    idades += [(i * 10.0) - 95.0 for i in range(10, 40)]   # cresce de 5 a 295
    _serie(s, "106", t0, idades)
    s.add_sample(t0 + 400, "106", "last_frame_age_s", 2.0)  # frame em t0+398

    outs = outages(s, "106", t0, t0 + 500, min_s=30)

    assert len(outs) == 1
    assert outs[0].start == t0 + 95
    assert outs[0].end == t0 + 398
    assert outs[0].duration_s == 303.0
    s.close()


def test_instabilidade_abaixo_do_piso_nao_vira_queda(tmp_path):
    """Reconexao de 20s some com piso de 30s - e' o ruido que polui o grafico."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    _serie(s, "106", t0, [5, 5, 15, 25, 5, 5, 5, 5])   # pico de 25s

    assert outages(s, "106", t0, t0 + 200, min_s=30) == []
    s.close()


def test_mesma_serie_vira_queda_com_piso_menor(tmp_path):
    """O piso e' o unico botao: a mesma serie muda de leitura conforme o zoom."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    _serie(s, "106", t0, [5, 5, 15, 25, 5, 5, 5, 5])

    assert len(outages(s, "106", t0, t0 + 200, min_s=20)) == 1
    s.close()


def test_monitor_fora_do_ar_nao_conta_como_no_ar(tmp_path):
    """Buraco na serie e' ignorancia, nao saude. Tem que sair separado."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    _serie(s, "106", t0, [5, 5, 5])                       # ate t0+20
    s.add_sample(t0 + 3600, "106", "last_frame_age_s", 5.0)  # volta 1h depois

    assert outages(s, "106", t0, t0 + 4000, min_s=30) == []
    buracos = coverage_gaps(s, "106", t0, t0 + 4000)
    assert buracos == [(t0 + 20, t0 + 3600),     # monitor fora no meio
                       (t0 + 3600, t0 + 4000)]   # e sem amostra ate agora
    s.close()


def test_queda_ainda_aberta_no_fim_da_janela(tmp_path):
    """Camera que caiu e nao voltou: end=None, duracao contada ate agora."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    idades = [5.0] + [(i * 10.0) - 5.0 for i in range(1, 30)]
    _serie(s, "106", t0, idades)

    outs = outages(s, "106", t0, t0 + 300, min_s=30)

    assert len(outs) == 1
    assert outs[0].start == t0 + 5
    assert outs[0].end is None
    s.close()


def test_motivo_vem_do_ultimo_disconnect_dentro_da_queda(tmp_path):
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    idades = [5.0] * 2 + [(i * 10.0) - 15.0 for i in range(2, 30)]
    _serie(s, "106", t0, idades)
    s.add_event(t0 + 18, "106", "DISCONNECT", "Timeout")
    s.add_event(t0 + 60, "106", "DISCONNECT", "Header truncado (2/4 bytes)")
    s.add_event(t0 + 900, "106", "DISCONNECT", "fora da janela")

    outs = outages(s, "106", t0, t0 + 300, min_s=30)

    assert outs[0].reason == "Header truncado (2/4 bytes)"
    s.close()


def test_disponibilidade_recorta_queda_que_comeca_antes_da_janela(tmp_path):
    """Queda de 1h que entrou 10min na janela conta 10min, nao 1h."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    janela = 600.0
    s.add_sample(t0 + 10, "106", "last_frame_age_s", 3000.0)   # caiu bem antes
    for i in range(2, 60):
        s.add_sample(t0 + i * 10, "106", "last_frame_age_s", 3000.0 + i * 10)

    outs = outages(s, "106", t0, t0 + janela, min_s=30)
    assert outs[0].start < t0                                   # inicio real
    assert availability(outs, t0, t0 + janela) == 0.0
    s.close()


def test_disponibilidade_sem_queda_e_cem_por_cento(tmp_path):
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    _serie(s, "105", t0, [5] * 60)

    assert availability(outages(s, "105", t0, t0 + 600, min_s=30),
                        t0, t0 + 600) == 100.0
    s.close()


def test_periodo_antes_da_primeira_amostra_e_desconhecido(tmp_path):
    """Janela de 24h com 1h de dados nao pode virar 'no ar 100%' nas 23h que
    o monitor nem existia."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    for i in range(360):
        s.add_sample(t0 + 82800 + i * 10, "102", "last_frame_age_s", 5.0)

    gaps = coverage_gaps(s, "102", t0, t0 + 86400)

    assert gaps[0] == (t0, t0 + 82800)
    s.close()


def test_periodo_depois_da_ultima_amostra_e_desconhecido(tmp_path):
    """Monitor caiu ha 2h: o grafico nao pode seguir pintando de verde."""
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    for i in range(60):
        s.add_sample(t0 + i * 10, "102", "last_frame_age_s", 5.0)

    gaps = coverage_gaps(s, "102", t0, t0 + 7200)

    assert gaps[-1] == (t0 + 590, t0 + 7200)
    s.close()


def test_store_vazia_e_janela_inteira_desconhecida(tmp_path):
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0

    assert coverage_gaps(s, "102", t0, t0 + 3600) == [(t0, t0 + 3600)]
    s.close()


def test_disponibilidade_sem_nada_conhecido_e_none(tmp_path):
    """None e' 'nao sei'. 100% seria mentira e 0% tambem."""
    t0 = 1_700_000_000.0
    assert availability([], t0, t0 + 3600, gaps=[(t0, t0 + 3600)]) is None


def test_disconnect_times_recorta_a_janela(tmp_path):
    """A pista de desconexao precisa dos instantes, e store.events nao tem
    filtro de fim."""
    from monitor.uptime import disconnect_times
    s = Store(tmp_path / "m.db")
    t0 = 1_700_000_000.0
    s.add_event(t0 - 10, "106", "DISCONNECT", "antes")
    s.add_event(t0 + 30, "106", "DISCONNECT", "dentro")
    s.add_event(t0 + 90, "106", "DISCONNECT", "depois")
    s.add_event(t0 + 30, "102", "DISCONNECT", "outra camera")
    s.add_event(t0 + 30, "106", "CONNECT", "outro tipo")

    assert disconnect_times(s, "106", t0, t0 + 60) == [t0 + 30]
    s.close()
