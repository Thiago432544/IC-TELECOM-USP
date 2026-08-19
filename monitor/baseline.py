"""Linha de base por camera e hora do dia: mediana de 7 dias."""
from __future__ import annotations
import statistics
from datetime import datetime
from typing import Optional

from monitor.store import Store


def hourly_baseline(store: Store, camera: str, hour: int, now: float,
                    days: int = 7, min_samples: int = 20) -> Optional[float]:
    vals = [v for ts, v in store.samples(camera, "frames_min", now - days * 86400)
            if datetime.fromtimestamp(ts).hour == hour]
    if len(vals) < min_samples:
        return None
    return statistics.median(vals)
