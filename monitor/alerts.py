"""Maquina de estados dos alertas. evaluate() e chamada a cada ~30s."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from monitor.config import AlertCfg
from monitor.metrics import fmt_dur

# Janela do auto-alarme: erros de worker sao contados nela, e o lembrete
# respeita o mesmo periodo. service.py importa daqui para nao divergir.
WORKER_WINDOW_S = 3600.0


@dataclass(frozen=True)
class CamState:
    frames_per_min: Optional[float]
    last_frame_age_s: Optional[float]
    baseline: Optional[float]
    disconnects_15min: int
    # Radio do CPE da propria camera, ja filtrado por frescor. RSRQ nao entra
    # de proposito: nesta rede privada quase vazia ele mede ocupacao de celula
    # e nao qualidade de caminho - fica colado perto do teto de -3 dB e quase
    # nao se mexe, entao alertar por ele seria ruido. Ver o diario de 09/09.
    rsrp_cam: Optional[float] = None
    sinr: Optional[float] = None


@dataclass(frozen=True)
class Snapshot:
    cameras: dict[str, CamState]
    # None = sem leitura recente. Nao e' "tem espaco": e' "nao sei".
    disk_free_gb: Optional[float]
    rsrp: Optional[float]
    rsrq: Optional[float]
    # excecoes de worker na ultima hora; ver o alarme proprio em evaluate()
    worker_errors: int = 0


@dataclass(frozen=True)
class Alert:
    kind: str
    origin: str
    text: str
    wants_chart: bool = False
    # entra no chat sem notificar o celular
    silent: bool = False


def _texto_down(cam: str, age: Optional[float]) -> str:
    """"869 min" nao se le: a duracao sai legivel ("20min", "15h05")."""
    return f"Camera {cam} SEM FRAMES ha {fmt_dur(age or 0)}."


def _texto_volta(cam: str, fora_s: Optional[float]) -> str:
    if fora_s is None or fora_s <= 0:
        return f"Camera {cam} voltou a enviar frames."
    return f"Camera {cam} voltou a enviar frames (fora por {fmt_dur(fora_s)})."


class AlertEngine:
    def __init__(self, cfg: AlertCfg):
        self.cfg = cfg
        self._down: set[str] = set()
        # hora do ultimo frame antes de cada queda: a volta diz quanto durou
        self._down_since: dict[str, float] = {}
        # cameras cujo down/recovered foi calado pelo flapping - guardadas para
        # que a estabilizacao ainda renda uma mensagem no fim da tempestade
        self._mudo: set[str] = set()
        self._deg_since: dict[str, float] = {}
        # ja avisou nesta degradacao? o primeiro notifica, o resto e' mudo
        self._deg_avisado: set[str] = set()
        self._link_bad_since: Optional[float] = None
        self._radio_bad_since: dict[str, float] = {}
        self._worker_avisado = False
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

    # -- partida ---------------------------------------------------------
    def prime(self, now: float, snap: Snapshot) -> None:
        """Adota quem ja estava fora, sem alertar. Chamado uma vez, ao subir.

        O `_down` vive so na memoria, entao todo restart redescobria as quedas
        antigas como novas - e com duas cameras fora ao mesmo tempo isso vira
        `group_down`, que diz "provavel enlace/eNodeB". Em 30/08/2026 ele
        disparou com a 102 e a 106 fora e apontou para o lado errado: a eNodeB
        mostrou as duas anexadas, com o radio de pe e uplink zero. O que estava
        morto era a Raspberry das duas.

        Quem cair depois disto alerta normalmente; de quem ja estava fora, a
        proxima mensagem e' a da volta (com a duracao inteira da queda).
        """
        for cam, st in snap.cameras.items():
            idade = st.last_frame_age_s
            if idade is not None and idade >= self.cfg.down_after_s:
                self._down.add(cam)
                self._down_since[cam] = now - idade
                self._last_sent[(cam, "down")] = now

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

            # Uma mensagem por queda e uma pela volta, nada no meio. Ate
            # 16/10/2026 a queda sustentada insistia a cada `realert_s`; com a
            # volta sempre avisada, o silencio depois do "caiu" ja quer dizer
            # "continua fora", e o lembrete de meia em meia hora era so ruido.
            if is_down and cam not in self._down:
                self._down.add(cam)
                self._down_since[cam] = now - age
                caiu.append(cam)
                if flapando:
                    self._mudo.add(cam)
                else:
                    avisar.append(cam)
            elif is_down and cam in self._mudo and not flapando:
                # a tempestade passou e ela ficou fora: o aviso que o flapping
                # calou sai agora, uma vez
                self._mudo.discard(cam)
                self._last_sent[(cam, "down")] = now
                out.append(Alert("down", cam, _texto_down(cam, age), True))
            elif not is_down and cam in self._down and age is not None and age < 60:
                self._down.discard(cam)
                self._clear(cam, "down")
                desde = self._down_since.pop(cam, None)
                fora = None if desde is None else (now - age) - desde
                if flapando:
                    self._mudo.add(cam)
                elif self._ok_to_send(now, cam, "recovered", interval=1):
                    out.append(Alert("recovered", cam, _texto_volta(cam, fora),
                                     False))

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
                    # Mesmo tratamento da queda sustentada (2543838): a linha de
                    # base e' historica, entao uma reducao proposital de
                    # frequencia segura a camera abaixo dela por horas, e o
                    # alerta repetia vibrando o celular a cada 30 min.
                    primeiro = cam not in self._deg_avisado
                    self._deg_avisado.add(cam)
                    out.append(Alert(
                        "degraded", cam,
                        f"Camera {cam} degradada: {st.frames_per_min:.1f} frames/min "
                        f"(linha de base da hora: {st.baseline:.1f}).", True,
                        silent=not primeiro))
            else:
                self._deg_since.pop(cam, None)
                # Fim do episodio: sem limpar a supressao, uma degradacao nova
                # nos 30 min seguintes seria engolida pela janela da anterior.
                self._deg_avisado.discard(cam)
                self._clear(cam, "degraded")

            # radio da propria camera: o que chega no poste. O alerta de
            # `link` olha o CPE do lado do SPA, que fica a 400 m da estacao e
            # le saturado - ele nao diz nada sobre as cameras.
            ruim = ((st.rsrp_cam is not None
                     and st.rsrp_cam < self.cfg.radio_rsrp_min)
                    or (st.sinr is not None
                        and st.sinr < self.cfg.radio_sinr_min))
            if ruim:
                self._radio_bad_since.setdefault(cam, now)
                if (now - self._radio_bad_since[cam] >= self.cfg.radio_after_s
                        and self._ok_to_send(now, cam, "radio")):
                    out.append(Alert(
                        "radio", cam,
                        f"Camera {cam} com radio ruim: RSRP={st.rsrp_cam} dBm, "
                        f"SINR={st.sinr} dB.", True, silent=False))
            else:
                self._radio_bad_since.pop(cam, None)
                self._clear(cam, "radio")

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

        # monitor cego: _forever() captura a excecao de qualquer worker e grava
        # worker_error, mas ninguem lia. Um worker que estoura todo ciclo deixa
        # o monitor no ar, com o /status respondendo, e a medicao parada - a
        # falha silenciosa que este projeto existe para eliminar, dentro do
        # proprio vigia. Erro avulso nao acorda ninguem; erro que repete, sim.
        if snap.worker_errors >= self.cfg.worker_errors_max:
            if self._ok_to_send(now, "monitor", "worker",
                                interval=WORKER_WINDOW_S):
                primeiro = not self._worker_avisado
                self._worker_avisado = True
                out.append(Alert(
                    "worker", "monitor",
                    f"Monitor com {snap.worker_errors} falhas internas na ultima "
                    f"hora - a medicao pode ter parado.", False,
                    silent=not primeiro))
        else:
            self._worker_avisado = False
            self._clear("monitor", "worker")

        # disco (re-alerta diario). Sem leitura recente o engine nao opina:
        # ate 09/09/2026 o service passava 1e9 GB quando faltava amostra, e uma
        # falha de leitura virava "disco folgado", calando o alerta bem quando
        # o disco deixava de ser observado.
        if snap.disk_free_gb is not None and snap.disk_free_gb < self.cfg.disk_min_gb:
            if self._ok_to_send(now, "pc", "disk", interval=86400):
                out.append(Alert("disk", "pc",
                                 f"Disco D: com {snap.disk_free_gb:.0f} GB livres "
                                 f"(minimo {self.cfg.disk_min_gb:.0f} GB).", False))
        else:
            self._clear("pc", "disk")

        return out
