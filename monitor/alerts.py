"""Maquina de estados dos alertas. evaluate() e chamada a cada ~30s."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from monitor.config import AlertCfg
from monitor.metrics import fmt_dur


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
    # entra no chat sem notificar o celular
    silent: bool = False


def _texto_down(cam: str, age: Optional[float]) -> str:
    """"869 min" nao se le. Na terceira insistencia de uma queda longa, a
    duracao e' a unica coisa nova na mensagem - precisa sair legivel."""
    return f"Camera {cam} SEM FRAMES ha {fmt_dur(age or 0)}."


class AlertEngine:
    def __init__(self, cfg: AlertCfg):
        self.cfg = cfg
        self._down: set[str] = set()
        # cameras cujo down/recovered foi calado pelo flapping - guardadas para
        # que a estabilizacao ainda renda uma mensagem no fim da tempestade
        self._mudo: set[str] = set()
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
        # "caiu agora" e "vale avisar" divergem quando o flapping cala alguem:
        # simultaneidade entre cameras e' diagnostico proprio (enlace/eNodeB) e
        # nao pode sumir so porque uma delas estava em tempestade.
        caiu: list[str] = []
        avisar: list[str] = []

        for cam, st in snap.cameras.items():
            age = st.last_frame_age_s
            is_down = age is not None and age >= self.cfg.down_after_s
            # Enquanto flapa, o par down/recovered se repete a cada 2 min e
            # afoga tudo: em 48h a 106 rendeu 135 dos 140 alertas, e a 102 que
            # caiu de vez virou uma linha no meio. O flapping ja e' o alerta.
            flapando = st.disconnects_15min >= self.cfg.flap_count

            if is_down and cam not in self._down:
                self._down.add(cam)
                caiu.append(cam)
                if flapando:
                    self._mudo.add(cam)
                else:
                    avisar.append(cam)
            elif is_down and not flapando and self._ok_to_send(now, cam, "down"):
                # Queda sustentada tem que insistir: sem isto, uma camera fora
                # ha 15h gera uma unica mensagem, e o silencio depois dela e'
                # indistinguivel de problema resolvido.
                #
                # Insistir, porem, nao e' acordar. Quem recebeu o primeiro
                # aviso ja sabe da queda; vibrar de meia em meia hora a noite
                # inteira so ensina a ignorar o alerta - e ai o proximo, o que
                # importa, tambem passa batido. O lembrete fica no chat, mudo,
                # para quem for olhar. So o primeiro down notifica.
                out.append(Alert("down", cam, _texto_down(cam, age), True,
                                 silent=True))
            elif not is_down and cam in self._down and age is not None and age < 60:
                self._down.discard(cam)
                self._clear(cam, "down")
                if flapando:
                    self._mudo.add(cam)
                elif self._ok_to_send(now, cam, "recovered", interval=1):
                    out.append(Alert("recovered", cam,
                                     f"Camera {cam} voltou a enviar frames.", False))

            if cam in self._mudo and not is_down and not flapando:
                # fim da tempestade: quem ficou calado precisa de um encerramento
                self._mudo.discard(cam)
                if self._ok_to_send(now, cam, "recovered", interval=1):
                    out.append(Alert("recovered", cam,
                                     f"Camera {cam} estabilizou e esta enviando "
                                     f"frames.", False))

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
        if len(caiu) >= 2:
            names = ", ".join(sorted(caiu))
            for cam in caiu:
                self._last_sent[(cam, "down")] = now
                self._mudo.discard(cam)
            out.append(Alert("group_down", "*",
                             f"{len(caiu)} cameras sem frames ao mesmo tempo "
                             f"({names}) - provavel enlace/eNodeB.", True))
        else:
            for cam in avisar:
                if self._ok_to_send(now, cam, "down"):
                    out.append(Alert("down", cam,
                                     _texto_down(cam, snap.cameras[cam].last_frame_age_s),
                                     True))

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
