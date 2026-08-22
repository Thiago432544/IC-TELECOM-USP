"""Taxa de entrega por camera, observando D:\\SPA_Data por mtime (somente leitura)."""
from __future__ import annotations
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Taxa medida nesta janela, nao no intervalo entre dois polls. Com poll de
# 10s e save_every=10, cada arquivo visto valia 60 f/min: a metrica so
# conseguia assumir 0, 60, 120, 180. Uma camera entregando 30 f/min virava uma
# onda quadrada 0/120 - ilegivel no grafico e incapaz de sustentar os 600s
# continuos abaixo do limiar que o alerta de degradada exige.
RATE_WINDOW_S = 300.0


@dataclass(frozen=True)
class CameraSample:
    camera: str
    frames_per_min: Optional[float]
    last_frame_age_s: Optional[float]


def _scan(day_dir: Path, since: float) -> tuple[int, Optional[float]]:
    """(arquivos com mtime > since, maior mtime) — 0/None se a pasta nao existe."""
    count, newest = 0, None
    try:
        with os.scandir(day_dir) as it:
            for e in it:
                if not e.is_file():
                    continue
                m = e.stat().st_mtime
                if m > since:
                    count += 1
                if newest is None or m > newest:
                    newest = m
    except OSError:
        pass
    return count, newest


class FrameWatcher:
    def __init__(self, images_dir: Path, cameras: dict[str, int],
                 rate_window_s: float = RATE_WINDOW_S):
        self._root = Path(images_dir)
        self._cameras = dict(cameras)
        self._rate_window = float(rate_window_s)
        # por camera: (inicio, fim, arquivos) de cada poll ainda na janela
        self._hist: dict[str, list[tuple[float, float, int]]] = {
            c: [] for c in self._cameras}
        self._last_poll: Optional[float] = None

    def _day_dir(self, camera: str, ts: float) -> Path:
        return self._root / time.strftime("%Y_%m_%d", time.localtime(ts)) / camera

    def poll(self, now: float) -> list[CameraSample]:
        out = []
        window = None if self._last_poll is None else now - self._last_poll
        for cam, save_every in self._cameras.items():
            since = self._last_poll if self._last_poll is not None else now
            n_today, newest = _scan(self._day_dir(cam, now), since)
            n_yest, newest_y = _scan(self._day_dir(cam, now - 86400), since)
            newest_any = max((m for m in (newest, newest_y) if m is not None),
                             default=None)
            rate = None
            if window and window > 0:
                rate = self._taxa(cam, save_every, self._last_poll, now,
                                  n_today + n_yest)
            age = (now - newest_any) if newest_any is not None else None
            out.append(CameraSample(cam, rate, age))
        self._last_poll = now
        return out

    def _taxa(self, cam: str, save_every: int, inicio: float, fim: float,
              n: int) -> Optional[float]:
        """Arquivos por toda a janela, e nao pelo ultimo intervalo.

        Um trecho que so cruza a borda entra inteiro - e o span cresce junto,
        entao contagem e tempo falam sempre do mesmo intervalo coberto.
        """
        h = self._hist.setdefault(cam, [])
        h.append((inicio, fim, n))
        corte = fim - self._rate_window
        while len(h) > 1 and h[0][1] <= corte:
            h.pop(0)
        span = fim - h[0][0]
        if span <= 0:
            return None
        return sum(x[2] for x in h) * save_every * 60.0 / span
