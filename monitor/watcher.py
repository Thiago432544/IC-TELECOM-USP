"""Taxa de entrega por camera, observando D:\\SPA_Data por mtime (somente leitura)."""
from __future__ import annotations
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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
    def __init__(self, images_dir: Path, cameras: dict[str, int]):
        self._root = Path(images_dir)
        self._cameras = dict(cameras)
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
                rate = (n_today + n_yest) * save_every * 60.0 / window
            age = (now - newest_any) if newest_any is not None else None
            out.append(CameraSample(cam, rate, age))
        self._last_poll = now
        return out
