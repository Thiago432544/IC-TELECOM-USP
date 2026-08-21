"""Registry de metricas e a relacao entre janela e piso de queda.

Adicionar uma metrica nova (quando a Fase 3 subir) e' acrescentar uma linha em
METRICS. Nem charts.py nem bot.py mudam.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

# Janelas dos botoes do Telegram, na ordem em que aparecem.
BUTTON_WINDOWS = (1800, 3600, 7200, 43200, 86400)
DEFAULT_WINDOW = 86400

# Uma queda so entra no grafico se ocupar ~0,2% da largura; abaixo disso e'
# fina demais para ser vista e so serve para poluir. O piso nunca desce de 30s
# porque com save_every=10 a idade de uma camera saudavel ja oscila ate ~10s.
FLOOR_LADDER = (30, 60, 120, 300, 600, 900, 1800)
FLOOR_MIN = 30
_FLOOR_DIVISOR = 500.0

_DUR = re.compile(r"^(\d+)(s|m|min|h|d)$")
_MULT = {"s": 1, "m": 60, "min": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True)
class MetricSpec:
    key: str          # nome canonico, o que aparece na legenda
    label: str        # texto do eixo
    unit: str
    kind: str         # "outages" (faixa de disponibilidade) | "series" (linha)
    origin: str       # "camera" | "cpe" | "pc"
    sample: Optional[str]   # nome na store; None quando e' derivada
    phase: int        # 2 = ja coletado hoje; 3 = depende do agente na Rasp
    aliases: tuple[str, ...] = ()


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("conexao", "no ar", "%", "outages", "camera", None, 2,
               ("conexão", "conn", "uptime", "quedas", "queda", "up")),
    MetricSpec("frames", "frames por minuto", "f/min", "series", "camera",
               "frames_min", 2, ("fps", "taxa", "frame")),
    MetricSpec("idade", "idade do ultimo frame", "s", "series", "camera",
               "last_frame_age_s", 2, ("atraso", "age")),
    MetricSpec("rsrp", "RSRP do CPE do SPA", "dBm", "series", "cpe",
               "rsrp", 2, ()),
    MetricSpec("rsrq", "RSRQ do CPE do SPA", "dB", "series", "cpe",
               "rsrq", 2, ()),
    MetricSpec("disco", "espaco livre", "GB", "series", "pc",
               "disk_free_gb", 2, ("disk", "hd")),
    MetricSpec("cpu", "uso de CPU", "%", "series", "camera",
               "cpu_pct", 3, ("carga", "load")),
    MetricSpec("temperatura", "temperatura do SoC", "C", "series", "camera",
               "temp_c", 3, ("temp",)),
    MetricSpec("memoria", "memoria livre", "MB", "series", "camera",
               "mem_free_mb", 3, ("memória", "mem", "ram")),
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


_INDEX = {}
for _m in METRICS:
    for _name in (_m.key,) + _m.aliases:
        _INDEX[_norm(_name)] = _m


def find_metric(name: str) -> Optional[MetricSpec]:
    return _INDEX.get(_norm(name))


def parse_duration(tok: str) -> Optional[int]:
    m = _DUR.match(_norm(tok))
    return int(m.group(1)) * _MULT[m.group(2)] if m else None


def label_duration(s: float) -> str:
    s = int(s)
    if s % 86400 == 0 and s >= 2 * 86400:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}min"
    return f"{s}s"


def outage_floor(window_s: float, forcado: Optional[float] = None) -> int:
    """Piso de duracao para uma queda contar, dado o zoom.

    Zoom out mostra so o que derruba; zoom in mostra o flap. E' a mesma
    funcao servindo as duas perguntas sem nunca saturar.
    """
    if forcado is not None:
        return max(FLOOR_MIN, int(forcado))
    alvo = window_s / _FLOOR_DIVISOR
    return next((r for r in FLOOR_LADDER if r >= alvo), FLOOR_LADDER[-1])
