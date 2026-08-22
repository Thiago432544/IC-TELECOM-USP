"""Gramatica do /grafico, botoes de janela e callbacks."""
from pathlib import Path

import pytest

from monitor.bot import BotHandler
from monitor.config import load_config
from monitor.store import Store

PNG = b"\x89PNG\r\n\x1a\n"
NOW = 1_700_000_000.0


@pytest.fixture
def bot(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    for i in range(360):
        s.add_sample(NOW - 3600 + i * 10, "102", "last_frame_age_s", 5.0)
        s.add_sample(NOW - 3600 + i * 10, "102", "frames_min", 55.0)
    yield BotHandler(s, cfg)
    s.close()


def test_status_continua_listando_as_cameras(bot):
    r = bot.handle("/status", NOW)
    assert r.png is None
    for cam in ("102", "105", "106"):
        assert cam in r.text


def test_grafico_sem_argumento_usa_conexao_em_24h(bot):
    r = bot.handle("/grafico 102", NOW)
    assert r.png[:8] == PNG
    assert "24h" in r.text and "imagem" in r.text


def test_grafico_aceita_a_janela(bot):
    assert "2h" in bot.handle("/grafico 102 2h", NOW).text


def test_grafico_aceita_metrica_e_janela(bot):
    r = bot.handle("/grafico 102 conexao 30min", NOW)
    assert "30min" in r.text and r.png[:8] == PNG


def test_ordem_dos_argumentos_nao_importa(bot):
    """No celular a gente digita na ordem que vier."""
    a = bot.handle("/grafico 102 conexao 2h", NOW)
    b = bot.handle("/grafico 2h 102 conexao", NOW)
    c = bot.handle("/grafico conexao 2h 102", NOW)
    assert a.text == b.text == c.text


def test_piso_aparece_sempre_na_resposta(bot):
    """Numero que muda o que voce ve nao pode ficar implicito."""
    assert ">=5min" in bot.handle("/grafico 102 24h", NOW).text
    assert ">=30s" in bot.handle("/grafico 102 30min", NOW).text


def test_piso_pode_ser_forcado_com_uma_segunda_duracao(bot):
    assert ">=30s" in bot.handle("/grafico 102 24h 30s", NOW).text


def test_botoes_cobrem_as_cinco_janelas_e_marcam_a_atual(bot):
    r = bot.handle("/grafico 102 2h", NOW)
    labels = [t for t, _ in r.buttons]
    dados = [d for _, d in r.buttons]

    assert len(r.buttons) == 5
    assert any(l.endswith("30min") for l in labels)
    assert labels[2] == "•2h"                      # a atual, marcada
    assert dados[2] == "g:102:conexao:7200"
    assert all(len(d.encode()) <= 64 for d in dados)


def test_botao_preserva_a_metrica_escolhida(bot):
    r = bot.handle("/grafico 102 frames 2h", NOW)
    assert all(d.startswith("g:102:frames:") for _, d in r.buttons)


def test_callback_redesenha_na_janela_do_botao(bot):
    r = bot.handle_callback("g:102:conexao:3600", NOW)
    assert r.png[:8] == PNG
    assert "1h" in r.text


def test_callback_com_lixo_nao_estoura(bot):
    assert bot.handle_callback("qualquer coisa", NOW).png is None


def test_metrica_da_fase_3_avisa_em_vez_de_fingir(bot):
    r = bot.handle("/grafico 102 memoria", NOW)
    assert "Fase 3" in r.text
    assert r.png[:8] == PNG


def test_camera_desconhecida_cai_na_ajuda(bot):
    assert "/grafico" in bot.handle("/grafico 999", NOW).text
    assert bot.handle("/grafico 999", NOW).png is None


def test_metrica_desconhecida_aponta_para_metricas(bot):
    assert "/metricas" in bot.handle("/grafico 102 banana", NOW).text


def test_metricas_lista_o_que_existe_e_o_que_ainda_nao(bot):
    txt = bot.handle("/metricas", NOW).text
    assert "conexao" in txt and "memoria" in txt and "Fase 3" in txt


def test_comando_desconhecido_recebe_ajuda(bot):
    r = bot.handle("/xyz", NOW)
    assert "/status" in r.text and r.png is None


def test_resposta_traz_o_motivo_da_maior_queda(tmp_path):
    """Ver a queda sem o motivo obriga a ir no log; o motivo ja esta na store."""
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    for i in range(60):
        s.add_sample(NOW - 3600 + i * 10, "106", "last_frame_age_s", 5.0)
    for i in range(60, 360):
        s.add_sample(NOW - 3600 + i * 10, "106", "last_frame_age_s",
                     (i - 60) * 10.0)
    s.add_event(NOW - 2990, "106", "DISCONNECT", "Header truncado (2/4 bytes)")

    r = BotHandler(s, cfg).handle("/grafico 106 1h", NOW)
    assert "Header truncado" in r.text
    s.close()


def test_status_usa_a_mesma_contagem_do_grafico(bot):
    """/status dizendo 50 quedas e /grafico dizendo 0 na mesma camera e' o tipo
    de contradicao que faz a pessoa parar de confiar nos dois."""
    for i in range(50):
        bot.store.add_event(NOW - 3000 + i * 30, "102", "DISCONNECT", "Timeout")

    txt = bot.handle("/status", NOW).text

    assert "sem intervalo" in txt
    assert "50" not in txt


def test_status_diz_a_janela_e_o_piso_no_cabecalho(bot):
    txt = bot.handle("/status", NOW).text
    assert "24h" in txt and ">=5min" in txt


def test_status_traz_disponibilidade_por_camera(bot):
    assert "imagem 100.0%" in bot.handle("/status", NOW).text


# -- metricas que nao sao de camera ------------------------------------------

def test_metrica_do_cpe_dispensa_o_numero_da_camera(bot):
    """RSRP nao pertence a nenhuma camera: exigir o numero era pedir um dado
    que nao existe, e a resposta era o texto de ajuda."""
    r = bot.handle("/grafico rsrp", NOW)
    assert r.png[:8] == PNG
    assert "cpe" in r.text.lower()
    assert not any(c in r.text for c in ("102", "105", "106"))


def test_metrica_do_pc_dispensa_o_numero_da_camera(bot):
    assert bot.handle("/grafico disco", NOW).png[:8] == PNG


def test_metrica_do_cpe_com_camera_nao_atribui_o_dado_a_ela(bot):
    """Quem digita '/grafico 106 rsrp' tem que ver 'cpe', nao '106': o radio
    lido e' o do CPE do lado do SPA, nao o da Rasp."""
    r = bot.handle("/grafico 106 rsrp", NOW)
    assert "cpe" in r.text.lower() and "106" not in r.text.split("\n")[0]


def test_botao_de_janela_do_cpe_volta_pelo_callback(bot):
    r = bot.handle("/grafico rsrq", NOW)
    dados = [d for _, d in r.buttons]
    assert "g:cpe:rsrq:3600" in dados
    assert bot.handle_callback("g:cpe:rsrq:3600", NOW).png[:8] == PNG


def test_cpe_desligado_explica_o_motivo_em_vez_de_so_sumir(bot):
    """config.example.toml tem [cpe] enabled = false - e' o caso real do SPA."""
    t = bot.handle("/grafico rsrp", NOW).text
    assert "enabled" in t and "probe" in t


def test_grafico_sem_camera_e_sem_metrica_ainda_pede_ajuda(bot):
    assert bot.handle("/grafico", NOW).png is None


def test_metrica_de_camera_sem_camera_continua_pedindo_a_camera(bot):
    assert bot.handle("/grafico conexao", NOW).png is None


def test_status_mostra_rsrp_e_rsrq(bot):
    bot.store.add_sample(NOW - 30, "cpe", "rsrp", -85.0)
    bot.store.add_sample(NOW - 30, "cpe", "rsrq", -17.0)
    t = bot.handle("/status", NOW).text
    assert "RSRP" in t and "RSRQ" in t


def test_status_diz_quando_o_cpe_esta_desligado(bot):
    """Sem essa linha, /status simplesmente omite o enlace e quem le supoe
    que esta tudo bem com ele."""
    t = bot.handle("/status", NOW).text
    assert "cpe" in t.lower() and "enabled" in t
