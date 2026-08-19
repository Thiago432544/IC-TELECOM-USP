"""Scraper do web UI do ELSYS Amplimax. Regexes vem do config: a pagina real
ainda nao foi capturada -- calibrar em campo com `python -m monitor.cpe --probe`."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable, Optional

from monitor.config import CpeCfg


@dataclass(frozen=True)
class CpeReading:
    rsrp: Optional[float]
    rsrq: Optional[float]
    connected: bool


def _default_get(url: str, auth) -> Optional[str]:
    import requests
    try:
        r = requests.get(url, auth=auth, timeout=10)
        return r.text if r.ok else None
    except Exception:
        return None


class CpeScraper:
    def __init__(self, cfg: CpeCfg, get: Optional[Callable] = None):
        self.cfg = cfg
        self._get = get or _default_get

    def fetch(self) -> Optional[CpeReading]:
        auth = (self.cfg.username, self.cfg.password) if self.cfg.username else None
        html = self._get(self.cfg.url, auth)
        if html is None:
            return None
        rsrp = _first_float(self.cfg.rsrp_re, html)
        rsrq = _first_float(self.cfg.rsrq_re, html)
        return CpeReading(rsrp, rsrq, "Conectado" in html)


def _first_float(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    from monitor.config import load_config

    ap = argparse.ArgumentParser(description="Sondagem do CPE para calibrar os regexes")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--config", default="config.toml")
    args = ap.parse_args()
    cfg = load_config(Path(args.config)).cpe
    auth = (cfg.username, cfg.password) if cfg.username else None
    html = _default_get(cfg.url, auth)
    if html is None:
        print("FALHA: sem resposta HTTP de", cfg.url)
    else:
        print(html)
        print("=" * 60)
        print("rsrp_re capturou:", _first_float(cfg.rsrp_re, html))
        print("rsrq_re capturou:", _first_float(cfg.rsrq_re, html))
        print("'Conectado' presente:", "Conectado" in html)
