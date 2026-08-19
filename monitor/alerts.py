"""Maquina de estados dos alertas. evaluate() e chamada a cada ~30s."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from monitor.config import AlertCfg


@dataclass(frozen=True)
class CamState:
    frames_per_min: Optional[float]
    last_frame_age_s: Optional[float]
    baseline: Optional[float]
    disconnects_15min: int


@dataclass(frozen=True)
class Snapshot:
    cameras: dict[str, CamState]
    disk_free_gb: float
    rsrp: Optional[float]
    rsrq: Optional[float]


@dataclass(frozen=True)
class Alert:
    kind: str
    origin: str
    text: str
    wants_chart: bool = False


class AlertEngine:
    def __init__(self, cfg: AlertCfg):
        self.cfg = cfg
        self._down: set[str] = set()
        self._deg_since: dict[str, float] = {}
        self._link_bad_since: Optional[float] = None
        self._last_sent: dict[tuple[str, str], float] = {}

    # -- supressao -------------------------------------------------------
    def _ok_to_send(self, now, origin, kind, interval=None) -> bool:
        last = self._last_sent.get((origin, kind))
        if last is not None and now - last < (interval or self.cfg.realert_s):
            return False
        self._last_sent[(origin, kind)] = now
        return True

    def _clear(self, origin, kind):
        self._last_sent.pop((origin, kind), None)

    # -- avaliacao -------------------------------------------------------
    def evaluate(self, now: float, snap: Snapshot) -> list[Alert]:
        out: list[Alert] = []
        newly_down: list[str] = []

        for cam, st in snap.cameras.items():
            age = st.last_frame_age_s
            is_down = age is not None and age >= self.cfg.down_after_s

            if is_down and cam not in self._down:
                self._down.add(cam)
                newly_down.append(cam)
            elif not is_down and cam in self._down and age is not None and age < 60:
                self._down.discard(cam)
                self._clear(cam, "down")
                if self._ok_to_send(now, cam, "recovered", interval=1):
                    out.append(Alert("recovered", cam,
                                     f"Camera {cam} voltou a enviar frames.", False))

            # degradada (nunca junto com down)
            if (not is_down and st.baseline is not None
                    and st.frames_per_min is not None
                    and st.frames_per_min < self.cfg.degraded_ratio * st.baseline):
                self._deg_since.setdefault(cam, now)
                if (now - self._deg_since[cam] > self.cfg.degraded_after_s
                        and self._ok_to_send(now, cam, "degraded")):
                    out.append(Alert(
                        "degraded", cam,
                        f"Camera {cam} degradada: {st.frames_per_min:.1f} frames/min "
                        f"(linha de base da hora: {st.baseline:.1f}).", True))
            else:
                self._deg_since.pop(cam, None)

            if (st.disconnects_15min >= self.cfg.flap_count
                    and self._ok_to_send(now, cam, "flapping")):
                out.append(Alert(
                    "flapping", cam,
                    f"Camera {cam}: {st.disconnects_15min} desconexoes em 15 min.", True))

        # down individual ou agrupado
        if len(newly_down) >= 2:
            names = ", ".join(sorted(newly_down))
            for cam in newly_down:
                self._last_sent[(cam, "down")] = now
            out.append(Alert("group_down", "*",
                             f"{len(newly_down)} cameras sem frames ao mesmo tempo "
                             f"({names}) - provavel enlace/eNodeB.", True))
        else:
            for cam in newly_down:
                if self._ok_to_send(now, cam, "down"):
                    st = snap.cameras[cam]
                    age_min = (st.last_frame_age_s or 0) / 60
                    out.append(Alert("down", cam,
                                     f"Camera {cam} SEM FRAMES ha {age_min:.0f} min.", True))

        # enlace
        bad = ((snap.rsrp is not None and snap.rsrp < self.cfg.link_rsrp_min)
               or (snap.rsrq is not None and snap.rsrq < self.cfg.link_rsrq_min))
        if bad:
            if self._link_bad_since is None:
                self._link_bad_since = now
            elif (now - self._link_bad_since > self.cfg.link_after_s
                  and self._ok_to_send(now, "cpe", "link")):
                out.append(Alert("link", "cpe",
                                 f"Enlace ruim: RSRP={snap.rsrp} dBm, RSRQ={snap.rsrq} dB.",
                                 False))
        else:
            self._link_bad_since = None

        # disco (re-alerta diario)
        if snap.disk_free_gb < self.cfg.disk_min_gb:
            if self._ok_to_send(now, "pc", "disk", interval=86400):
                out.append(Alert("disk", "pc",
                                 f"Disco D: com {snap.disk_free_gb:.0f} GB livres "
                                 f"(minimo {self.cfg.disk_min_gb:.0f} GB).", False))
        else:
            self._clear("pc", "disk")

        return out
