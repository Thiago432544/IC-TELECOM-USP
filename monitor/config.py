"""Carrega config.toml em dataclasses tipadas."""
from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
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
class RadioCfg:
    """Radio do CPE de cada camera, lido por SSH ate a Rasp.

    O PC nao alcanca 192.168.11.1 - e' a LAN atras do radio. Quem fala com o
    CPE e' o coletor que roda na Pi; o monitor so le o CSV que ele mantem.
    """
    enabled: bool = False
    interval_s: int = 60
    ssh_user: str = "pi"
    csv: str = "/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/radio.csv"
    # Amostra mais velha que isto nao entra: coletor parado mostrando numero de
    # ontem e' pior que numero nenhum.
    max_atraso_s: int = 600
    cameras: dict[str, str] = field(default_factory=dict)

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
    radio: RadioCfg
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
        # .get: o config.toml que ja roda no PC do SPA nao tem [charts] nem
        # [radio]. Exigir a secao faria o monitor nao subir no proximo restart.
        charts=ChartsCfg(**raw.get("charts", {})),
        radio=RadioCfg(**raw.get("radio", {})),
        summary_hour=raw["summary_hour"],
    )
