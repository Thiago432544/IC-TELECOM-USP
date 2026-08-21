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
    assert "24h" in r.text and "no ar" in r.text


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
