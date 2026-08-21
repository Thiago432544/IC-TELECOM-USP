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

    def send_text(self, text):
        self.calls.append(("send_text", text))
        return True

    def send_photo(self, png, caption, buttons=None):
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
