"""Radio do CPE de cada camera, lido por SSH ate a Raspberry.

O PC do SPA nao alcanca `192.168.11.1`: e' a LAN atras do radio, e so a Rasp
chega la. Entao quem fala com o CPE e' o coletor que roda na Pi
(`deploy/rasp/radio_log.py`, por cron), e o monitor apenas le a **ultima linha
do CSV** que ele mantem. E' a S6 do estudo de 25/08 - pull para observar, nunca
para controlar.

Falha de SSH nao e' erro, e' sinal: sem chave, sem rede ou com a Pi fora nao ha
amostra, e a ausencia dela ja aparece no /status como "sem leitura".

Duas armadilhas que este modulo evita de proposito:

- `BatchMode=yes` no ssh. Sem isso, uma chave ausente faz o ssh **perguntar a
  senha** e pendurar o worker para sempre, em silencio.
- amostra velha nao entra. Se o cron da Pi morrer, `tail -1` devolve a mesma
  linha para sempre, e o bot mostraria o RSRP da semana passada como se fosse
  de agora. Numero velho apresentado como atual e' pior que numero nenhum -
  e' o mesmo erro que o coverage_gaps do uptime.py existe para nao cometer.
"""
from __future__ import annotations

import csv
import io
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

_TS = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Leitura:
    ts: float                       # instante gravado pela Pi
    rsrp: Optional[float]
    rsrq: Optional[float]
    sinr: Optional[float]
    band: Optional[str]
    netstate: Optional[str]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_csv(texto: str) -> Optional[Leitura]:
    """Do par (cabecalho, ultima linha) para uma Leitura.

    Le por nome de coluna, e nao por posicao: se o coletor da Pi ganhar um
    campo novo, o monitor continua lendo os certos em vez de deslocar tudo.
    """
    linhas = [l for l in (texto or "").splitlines() if l.strip()]
    if len(linhas) < 2:
        return None
    linha = next(csv.DictReader(io.StringIO("\n".join(linhas[:1] + linhas[-1:]))),
                 None)
    if not linha or linha.get("ts") in (None, "ts"):
        return None                 # CSV so com cabecalho: ainda nao amostrou
    try:
        ts = time.mktime(time.strptime(linha["ts"], _TS))
    except (ValueError, TypeError):
        return None
    return Leitura(ts=ts,
                   rsrp=_num(linha.get("rsrp")),
                   rsrq=_num(linha.get("rsrq")),
                   sinr=_num(linha.get("sinr")),
                   band=linha.get("band") or None,
                   netstate=linha.get("netstate") or None)


def _ssh(user: str, host: str, comando: str, timeout: float = 20.0) -> str:
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=accept-new",
         f"{user}@{host}", comando],
        capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


class RadioReader:
    """corre() e' injetavel: o teste nao abre ssh nenhum."""

    def __init__(self, cfg, corre: Optional[Callable] = None):
        self.cfg = cfg
        self._corre = corre or _ssh

    def ler(self, host: str, now: float) -> Optional[Leitura]:
        alvo = self.cfg.csv
        try:
            saida = self._corre(self.cfg.ssh_user, host,
                                f"head -n 1 {alvo}; tail -n 1 {alvo}")
        except Exception:
            return None             # ssh ausente, timeout, host fora
        leitura = parse_csv(saida)
        if leitura is None:
            return None
        if now - leitura.ts > self.cfg.max_atraso_s:
            return None             # coletor parado na Pi
        return leitura
