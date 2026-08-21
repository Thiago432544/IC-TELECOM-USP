"""Carrega config.toml em dataclasses tipadas."""
from __future__ import annotations
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CameraCfg:
    save_every: int

@dataclass(frozen=True)
class PathsCfg:
    images: Path
    log: Path
    data: Path

@dataclass(frozen=True)
class TelegramCfg:
    enabled: bool
    token: str
    chat_id: str

@dataclass(frozen=True)
class CpeCfg:
    enabled: bool
    url: str
    username: str
    password: str
    rsrp_re: str
    rsrq_re: str
    interval_s: int

@dataclass(frozen=True)
class AlertCfg:
    disk_min_gb: float
    down_after_s: int
    degraded_ratio: float
    degraded_after_s: int
    flap_count: int
    flap_window_s: int
    realert_s: int
    link_rsrp_min: float
    link_rsrq_min: float
    link_after_s: int

@dataclass(frozen=True)
class ChartsCfg:
    outage_min_s: int = 0        # 0 = piso automatico, derivado da janela

@dataclass(frozen=True)
class PanelCfg:
    port: int

@dataclass(frozen=True)
class Config:
    cameras: dict[str, CameraCfg]
    paths: PathsCfg
    telegram: TelegramCfg
    cpe: CpeCfg
    alerts: AlertCfg
    panel: PanelCfg
    charts: ChartsCfg
    summary_hour: int


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config(
        cameras={k: CameraCfg(**v) for k, v in raw["cameras"].items()},
        paths=PathsCfg(**{k: Path(v) for k, v in raw["paths"].items()}),
        telegram=TelegramCfg(**raw["telegram"]),
        cpe=CpeCfg(**raw["cpe"]),
        alerts=AlertCfg(**raw["alerts"]),
        panel=PanelCfg(**raw["panel"]),
        # .get: o config.toml que ja roda no PC do SPA nao tem [charts]
        charts=ChartsCfg(**raw.get("charts", {})),
        summary_hour=raw["summary_hour"],
    )
