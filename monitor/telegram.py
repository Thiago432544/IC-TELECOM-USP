"""Cliente minimo da Bot API do Telegram, com outbox em disco para texto."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Callable, Optional

from monitor.config import TelegramCfg


def _default_post(url, data, files=None) -> bool:
    import requests
    try:
        r = requests.post(url, data=data, files=files, timeout=15)
        return r.ok
    except Exception:
        return False


def _markup(buttons) -> str:
    return json.dumps({"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in buttons]]})


def _foto(png: bytes) -> dict:
    return {"photo": ("chart.png", png, "image/png")}


class TelegramClient:
    def __init__(self, cfg: TelegramCfg, data_dir: Path,
                 post: Optional[Callable] = None):
        self.cfg = cfg
        self._post = post or _default_post
        self._outbox = Path(data_dir) / "outbox.jsonl"
        self._outbox.parent.mkdir(parents=True, exist_ok=True)

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.cfg.token}/{method}"

    def send_text(self, text: str) -> bool:
        ok = self._post(self._api("sendMessage"),
                        {"chat_id": self.cfg.chat_id, "text": text})
        if not ok:
            with open(self._outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "text": text}) + "\n")
        return ok

    def send_photo(self, png: bytes, caption: str, buttons=None) -> bool:
        data = {"chat_id": self.cfg.chat_id, "caption": caption}
        if buttons:
            data["reply_markup"] = _markup(buttons)
        ok = self._post(self._api("sendPhoto"), data,
                        files=_foto(png))
        if not ok:
            self.send_text(caption + " [grafico indisponivel na hora do envio]")
        return ok

    def edit_photo(self, chat_id, message_id, png: bytes, caption: str,
                   buttons=None) -> bool:
        """Troca a imagem na mensagem que ja esta no chat.

        Falha nao vai para a outbox de proposito: reenviar um grafico velho
        horas depois nao ajuda ninguem.
        """
        data = {"chat_id": chat_id, "message_id": message_id,
                "media": json.dumps({"type": "photo", "media": "attach://photo",
                                     "caption": caption})}
        if buttons:
            data["reply_markup"] = _markup(buttons)
        return self._post(self._api("editMessageMedia"), data, files=_foto(png))

    def answer_callback(self, callback_id: str, text: str = "") -> bool:
        """Sem isso o Telegram deixa o botao girando no celular."""
        return self._post(self._api("answerCallbackQuery"),
                          {"callback_query_id": callback_id, "text": text})

    def flush_outbox(self) -> int:
        if not self._outbox.exists():
            return 0
        lines = [l for l in self._outbox.read_text(encoding="utf-8").splitlines() if l]
        sent = 0
        rest = []
        for line in lines:
            msg = json.loads(line)
            stamp = time.strftime("%d/%m %H:%M", time.localtime(msg["ts"]))
            if self._post(self._api("sendMessage"),
                          {"chat_id": self.cfg.chat_id,
                           "text": f"[atrasada, de {stamp}] {msg['text']}"}):
                sent += 1
            else:
                rest.append(line)
        self._outbox.write_text("\n".join(rest) + ("\n" if rest else ""),
                                encoding="utf-8")
        return sent

    def get_updates(self, offset: int) -> list[dict]:
        import requests
        try:
            r = requests.get(self._api("getUpdates"),
                             params={"offset": offset, "timeout": 25}, timeout=35)
            return r.json().get("result", []) if r.ok else []
        except Exception:
            return []
