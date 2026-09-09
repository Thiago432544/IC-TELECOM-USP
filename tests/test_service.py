import time
from pathlib import Path
from monitor.config import load_config
from monitor.service import build_snapshot, should_send_summary
from monitor.store import Store

def test_build_snapshot_reads_store(tmp_path):
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    now = time.time()
    s.add_sample(now - 5, "102", "frames_min", 50.0)
    s.add_sample(now - 5, "102", "last_frame_age_s", 12.0)
    for i in range(5):
        s.add_event(now - 60 * i, "102", "DISCONNECT", "x")
    snap = build_snapshot(s, cfg, now, disk_free_gb=100.0)
    c = snap.cameras["102"]
    assert c.frames_per_min == 50.0 and c.last_frame_age_s == 12.0
    assert c.disconnects_15min == 5
    assert snap.cameras["105"].last_frame_age_s is None
    assert snap.disk_free_gb == 100.0
    s.close()

def test_should_send_summary_once_per_day():
    ts_0830 = time.mktime((2026, 8, 19, 8, 30, 0, 0, 0, -1))
    ts_0730 = time.mktime((2026, 8, 19, 7, 30, 0, 0, 0, -1))
    assert should_send_summary(None, ts_0830, 8) is True
    assert should_send_summary("2026-08-19", ts_0830, 8) is False
    assert should_send_summary("2026-08-18", ts_0730, 8) is False   # antes das 8
    assert should_send_summary("2026-08-18", ts_0830, 8) is True


class _FakeTg:
    """Grava o que o bot mandaria para o Telegram."""
    def __init__(self):
        self.calls = []

    def send_text(self, text, silent=False):
        self.calls.append(("send_text", text))
        return True

    def send_photo(self, png, caption, buttons=None, silent=False):
        self.calls.append(("send_photo", caption, buttons))
        return True

    def edit_photo(self, chat_id, message_id, png, caption, buttons=None):
        self.calls.append(("edit_photo", chat_id, message_id, caption))
        return True

    def answer_callback(self, cb_id, text=""):
        self.calls.append(("answer_callback", cb_id))
        return True


def _handler(tmp_path):
    from monitor.bot import BotHandler
    cfg = load_config(Path("config.example.toml"))
    s = Store(tmp_path / "m.db")
    return BotHandler(s, cfg), s


def test_comando_de_texto_sai_como_foto_com_botoes(tmp_path):
    from monitor.service import route_update
    h, s = _handler(tmp_path)
    tg = _FakeTg()

    route_update(h, tg, {"message": {"text": "/grafico 102"}}, time.time())

    tipo, _, buttons = tg.calls[0]
    assert tipo == "send_photo" and len(buttons) == 5
    s.close()


def test_toque_no_botao_edita_a_mensagem_em_vez_de_mandar_outra(tmp_path):
    """O ponto dos botoes e' nao encher o chat de graficos."""
    from monitor.service import route_update
    h, s = _handler(tmp_path)
    tg = _FakeTg()

    route_update(h, tg, {"callback_query": {
        "id": "cb1", "data": "g:102:conexao:7200",
        "message": {"message_id": 77, "chat": {"id": 999}}}}, time.time())

    tipos = [c[0] for c in tg.calls]
    assert "edit_photo" in tipos and "send_photo" not in tipos
    editou = [c for c in tg.calls if c[0] == "edit_photo"][0]
    assert editou[1] == 999 and editou[2] == 77
    s.close()


def test_callback_e_respondido_antes_de_desenhar(tmp_path):
    """Sem answerCallbackQuery o botao fica girando no celular ate o timeout."""
    from monitor.service import route_update
    h, s = _handler(tmp_path)
    tg = _FakeTg()

    route_update(h, tg, {"callback_query": {
        "id": "cb1", "data": "g:102:conexao:7200",
        "message": {"message_id": 77, "chat": {"id": 999}}}}, time.time())

    assert tg.calls[0][0] == "answer_callback"
    s.close()


def test_callback_com_lixo_ainda_e_respondido(tmp_path):
    from monitor.service import route_update
    h, s = _handler(tmp_path)
    tg = _FakeTg()

    route_update(h, tg, {"callback_query": {
        "id": "cb9", "data": "lixo",
        "message": {"message_id": 1, "chat": {"id": 2}}}}, time.time())

    assert ("answer_callback", "cb9") in tg.calls
    s.close()


def test_mensagem_que_nao_e_comando_e_ignorada(tmp_path):
    from monitor.service import route_update
    h, s = _handler(tmp_path)
    tg = _FakeTg()

    assert route_update(h, tg, {"message": {"text": "bom dia"}},
                        time.time()) is False
    assert tg.calls == []
    s.close()


def test_alerta_de_degradada_manda_o_grafico_de_frames():
    """Camera degradada esta conectada. O grafico de conexao mostraria ~100%
    no ar e contradiria o texto do proprio alerta."""
    from monitor.service import chart_metric_for
    assert chart_metric_for("degraded") == "frames"


def test_alerta_de_queda_manda_o_grafico_de_conexao():
    from monitor.service import chart_metric_for
    for kind in ("down", "group_down", "flapping"):
        assert chart_metric_for(kind) == "conexao"


def test_camera_fora_ha_dias_continua_gravando_idade(tmp_path):
    """O watcher varre so as pastas de hoje e de ontem. Passados dois dias sem
    frame ele devolve None, e ate 09/09/2026 o service simplesmente parava de
    gravar amostra.

    O estrago era duplo: o /status congelava no ultimo numero - a 102 disse
    "ha 1d19h" por duas semanas - e, sem amostra nenhuma, coverage_gaps() lia
    a janela inteira como "sem cobertura". Uma camera fora ha dias aparecia
    como "sem dados" e "sem intervalo", ou seja, uma queda real pintada de
    ignorancia.

    O instante do ultimo frame continua conhecido: e' `ts - valor` da ultima
    amostra. A idade de agora sai dai.
    """
    from monitor.service import record_samples
    from monitor.watcher import CameraSample

    s = Store(tmp_path / "m.db")
    now = time.time()
    ultimo_frame = now - 9 * 86400
    # amostra escrita enquanto o watcher ainda enxergava a camera: 40h depois
    # do ultimo frame, valendo 40h de idade
    s.add_sample(ultimo_frame + 40 * 3600, "102", "last_frame_age_s", 40 * 3600.0)

    record_samples(s, [CameraSample("102", 0.0, None)], now)

    [(_, idade)] = s.samples("102", "last_frame_age_s", now - 1)
    assert abs(idade - 9 * 86400) < 2
    s.close()


def test_idade_medida_pelo_watcher_vence_a_costurada(tmp_path):
    """A costura e' so para quando o watcher perde a camera. Enquanto ele ve o
    frame, quem manda e' a medicao - inverter a precedencia congelaria a serie
    numa camera saudavel."""
    from monitor.service import record_samples
    from monitor.watcher import CameraSample

    s = Store(tmp_path / "m.db")
    now = time.time()
    s.add_sample(now - 86400, "105", "last_frame_age_s", 3600.0)   # amostra velha

    record_samples(s, [CameraSample("105", 57.0, 4.0)], now)

    [(_, idade)] = s.samples("105", "last_frame_age_s", now - 1)
    assert idade == 4.0
    s.close()


def test_camera_que_nunca_entregou_frame_nao_ganha_idade_inventada(tmp_path):
    """Sem amostra anterior nao ha instante de ultimo frame para costurar.
    Gravar 0 ou `now` aqui inventaria uma camera que nunca existiu - "sem
    dados" e' a resposta honesta."""
    from monitor.service import record_samples
    from monitor.watcher import CameraSample

    s = Store(tmp_path / "m.db")
    now = time.time()

    record_samples(s, [CameraSample("106", 0.0, None)], now)

    assert s.samples("106", "last_frame_age_s", 0) == []
    assert s.samples("106", "frames_min", 0) != []      # a taxa continua indo
    s.close()


def test_queda_longa_deixa_de_ser_lida_como_sem_dados(tmp_path):
    """Efeito de segunda ordem do mesmo bug, e o que mais enganava.

    Sem amostra, coverage_gaps() devolvia a janela inteira como "sem
    cobertura", availability() virava None e outages() nao achava queda
    nenhuma: uma camera fora ha dias aparecia no /status como "sem dados" e
    "sem intervalo". Com a serie costurada, a queda volta a ser uma queda.
    """
    from monitor.service import record_samples
    from monitor.uptime import availability, coverage_gaps, outages
    from monitor.watcher import CameraSample

    s = Store(tmp_path / "m.db")
    now = time.time()
    ultimo_frame = now - 5 * 86400
    s.add_sample(now - 660, "106", "last_frame_age_s", (now - 660) - ultimo_frame)
    for k in range(10, 0, -1):
        record_samples(s, [CameraSample("106", 0.0, None)], now - k * 60)

    since = now - 600
    gaps = coverage_gaps(s, "106", since, now)
    outs = outages(s, "106", since, now, min_s=300)

    assert gaps == []
    assert len(outs) == 1
    assert abs(outs[0].start - ultimo_frame) < 2      # instante real do frame
    assert availability(outs, since, now, tuple(gaps)) == 0.0
    s.close()


def test_leitura_de_disco_velha_nao_vale_como_leitura(tmp_path):
    """Se o worker do disco morrer, a ultima amostra fica no banco para sempre.
    Usa-la como atual esconderia um disco que encheu depois - o mesmo erro do
    RSRP velho que o _radio_recente ja evitava no painel."""
    from monitor.service import disk_free

    s = Store(tmp_path / "m.db")
    now = time.time()

    assert disk_free(s, now) is None                      # nunca leu
    s.add_sample(now - 3600, "pc", "disk_free_gb", 500.0)
    assert disk_free(s, now) is None                      # leu ha uma hora
    s.add_sample(now - 30, "pc", "disk_free_gb", 42.0)
    assert disk_free(s, now) == 42.0
    s.close()
