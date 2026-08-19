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

    def send_photo(self, png: bytes, caption: str) -> bool:
        ok = self._post(self._api("sendPhoto"),
                        {"chat_id": self.cfg.chat_id, "caption": caption},
                        files={"photo": ("chart.png", png, "image/png")})
        if not ok:
            self.send_text(caption + " [grafico indisponivel na hora do envio]")
        return ok

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
