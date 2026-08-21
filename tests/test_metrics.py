"""Registry de metricas, parsing de janela e a escada de pisos."""
import pytest

from monitor.metrics import (BUTTON_WINDOWS, find_metric, label_duration,
                             outage_floor, parse_duration)


@pytest.mark.parametrize("tok,esperado", [
    ("30min", 1800), ("30m", 1800), ("1h", 3600), ("2h", 7200),
    ("12h", 43200), ("24h", 86400), ("7d", 604800), ("90s", 90),
])
def test_parse_duration_aceita_as_formas_que_a_gente_digita(tok, esperado):
    assert parse_duration(tok) == esperado


@pytest.mark.parametrize("tok", ["106", "conexao", "", "h", "2x", "-3h"])
def test_parse_duration_recusa_o_que_nao_e_duracao(tok):
    assert parse_duration(tok) is None


@pytest.mark.parametrize("janela,piso", [
    (1800, 30),      # 30min
    (3600, 30),      # 1h
    (7200, 30),      # 2h
    (43200, 120),    # 12h
    (86400, 300),    # 24h
    (604800, 1800),  # 7d
])
def test_piso_acompanha_o_zoom(janela, piso):
    """Piso fixo polui nas duas pontas: some com tudo em 30min, traz o ruido
    de volta em 7d. Ele tem que subir junto com a janela."""
    assert outage_floor(janela) == piso


def test_piso_nunca_desce_de_30s():
    """Com save_every=10 a idade de uma camera saudavel ja oscila ate ~10s;
    piso menor marcaria respiracao normal como queda."""
    assert outage_floor(60) == 30


def test_piso_pode_ser_forcado_na_mao():
    assert outage_floor(86400, forcado=30) == 30


@pytest.mark.parametrize("apelido", ["memoria", "memória", "mem", "ram"])
def test_apelidos_encontram_a_mesma_metrica(apelido):
    assert find_metric(apelido).key == "memoria"


@pytest.mark.parametrize("apelido,key", [
    ("conexao", "conexao"), ("conexão", "conexao"), ("quedas", "conexao"),
    ("frames", "frames"), ("fps", "frames"),
    ("temp", "temperatura"), ("cpu", "cpu"), ("rsrp", "rsrp"),
])
def test_registry_resolve_os_nomes_do_comando(apelido, key):
    assert find_metric(apelido).key == key


def test_metrica_desconhecida_devolve_none():
    assert find_metric("banana") is None


def test_conexao_e_a_metrica_padrao_e_ja_tem_dados():
    m = find_metric("conexao")
    assert m.kind == "outages" and m.phase == 2


def test_metricas_da_rasp_ficam_marcadas_como_fase_3():
    """cpu/temperatura/memoria dependem do agente que ainda nao existe;
    o bot precisa saber disso para responder direito em vez de plotar vazio."""
    for k in ("cpu", "temperatura", "memoria"):
        assert find_metric(k).phase == 3


def test_botoes_cobrem_as_janelas_pedidas():
    assert [label_duration(w) for w in BUTTON_WINDOWS] == \
        ["30min", "1h", "2h", "12h", "24h"]
