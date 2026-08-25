import json
from monitor.config import TelegramCfg
from monitor.telegram import TelegramClient

CFG = TelegramCfg(enabled=True, token="T", chat_id="C")

def test_send_text_posts_to_api(tmp_path):
    calls = []
    tc = TelegramClient(CFG, tmp_path, post=lambda url, data, files=None: calls.append((url, data)) or True)
    assert tc.send_text("oi") is True
    url, data = calls[0]
    assert "botT/sendMessage" in url and data["chat_id"] == "C" and data["text"] == "oi"

def test_failed_text_goes_to_outbox_and_flushes(tmp_path):
    ok = {"v": False}
    tc = TelegramClient(CFG, tmp_path, post=lambda *a, **k: ok["v"])
    assert tc.send_text("perdida") is False
    outbox = tmp_path / "outbox.jsonl"
    assert json.loads(outbox.read_text().splitlines()[0])["text"] == "perdida"
    ok["v"] = True
    assert tc.flush_outbox() == 1
    assert outbox.read_text().strip() == ""

def test_send_photo_uses_files(tmp_path):
    calls = []
    tc = TelegramClient(CFG, tmp_path,
                        post=lambda url, data, files=None: calls.append((url, files)) or True)
    assert tc.send_photo(b"\x89PNG...", "grafico") is True
    url, files = calls[0]
    assert "sendPhoto" in url and files["photo"][1] == b"\x89PNG..."


def _spy(tmp_path):
    calls = []
    tc = TelegramClient(CFG, tmp_path,
                        post=lambda url, data, files=None:
                            calls.append((url, data, files)) or True)
    return tc, calls


def test_send_photo_manda_os_botoes_como_reply_markup(tmp_path):
    tc, calls = _spy(tmp_path)
    tc.send_photo(b"PNG", "grafico",
                  buttons=[("30min", "g:106:conexao:1800"),
                           ("24h", "g:106:conexao:86400")])
    _, data, _ = calls[0]
    markup = json.loads(data["reply_markup"])
    assert markup["inline_keyboard"] == [[
        {"text": "30min", "callback_data": "g:106:conexao:1800"},
        {"text": "24h", "callback_data": "g:106:conexao:86400"}]]


def test_send_photo_sem_botoes_nao_manda_reply_markup(tmp_path):
    tc, calls = _spy(tmp_path)
    tc.send_photo(b"PNG", "grafico")
    assert "reply_markup" not in calls[0][1]


def test_edit_photo_troca_a_imagem_na_mesma_mensagem(tmp_path):
    """O ponto dos botoes e' nao encher o chat: tocou, a imagem troca no lugar."""
    tc, calls = _spy(tmp_path)
    tc.edit_photo("C", 42, b"PNG2", "nova legenda",
                  buttons=[("1h", "g:106:conexao:3600")])
    url, data, files = calls[0]

    assert "editMessageMedia" in url
    assert data["message_id"] == 42
    assert files["photo"][1] == b"PNG2"
    media = json.loads(data["media"])
    assert media["type"] == "photo"
    assert media["media"] == "attach://photo"
    assert media["caption"] == "nova legenda"


def test_answer_callback_responde_pelo_id(tmp_path):
    """Sem isso o Telegram deixa o botao girando no celular."""
    tc, calls = _spy(tmp_path)
    tc.answer_callback("cb-1")
    url, data, _ = calls[0]
    assert "answerCallbackQuery" in url
    assert data["callback_query_id"] == "cb-1"


def test_edit_photo_que_falha_nao_vai_para_a_outbox(tmp_path):
    """Reenviar um grafico velho horas depois nao ajuda ninguem."""
    tc = TelegramClient(CFG, tmp_path, post=lambda *a, **k: False)
    assert tc.edit_photo("C", 1, b"PNG", "x") is False
    assert not (tmp_path / "outbox.jsonl").exists()


def test_texto_silencioso_desliga_a_notificacao(tmp_path):
    tc, calls = _spy(tmp_path)
    tc.send_text("lembrete", silent=True)
    assert calls[0][1]["disable_notification"] == "true"


def test_texto_normal_nao_carrega_disable_notification(tmp_path):
    tc, calls = _spy(tmp_path)
    tc.send_text("caiu agora")
    assert "disable_notification" not in calls[0][1]


def test_foto_silenciosa_desliga_a_notificacao(tmp_path):
    tc, calls = _spy(tmp_path)
    tc.send_photo(b"PNG", "grafico", silent=True)
    assert calls[0][1]["disable_notification"] == "true"


def test_foto_silenciosa_que_falha_cai_para_texto_ainda_calado(tmp_path):
    """O lembrete de queda vai com grafico: se a foto falha, o texto que entra
    no lugar dela nao pode chegar vibrando."""
    calls = []

    def post(url, data, files=None):
        calls.append((url, data))
        return "sendPhoto" not in url

    tc = TelegramClient(CFG, tmp_path, post=post)
    tc.send_photo(b"PNG", "grafico", silent=True)
    url, data = calls[-1]
    assert "sendMessage" in url and data["disable_notification"] == "true"


def test_silencio_sobrevive_a_outbox(tmp_path):
    """Lembrete calado que ficou preso nao pode acordar ninguem horas depois."""
    ok = {"v": False}
    calls = []

    def post(url, data, files=None):
        calls.append(data)
        return ok["v"]

    tc = TelegramClient(CFG, tmp_path, post=post)
    assert tc.send_text("lembrete", silent=True) is False
    ok["v"] = True
    assert tc.flush_outbox() == 1
    assert calls[-1]["disable_notification"] == "true"


def test_outbox_gravada_antes_da_mudanca_ainda_sai(tmp_path):
    """A outbox no PC do SPA pode ter linhas sem a chave: barulhentas, como eram."""
    (tmp_path / "outbox.jsonl").write_text(
        json.dumps({"ts": 1000.0, "text": "de antes"}), encoding="utf-8")
    tc, calls = _spy(tmp_path)
    assert tc.flush_outbox() == 1
    assert "disable_notification" not in calls[0][1]
