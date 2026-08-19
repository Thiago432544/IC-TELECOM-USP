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
