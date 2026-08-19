"""Parser e seguidor incremental do LOG_connections.txt (somente leitura)."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_TS = "%Y-%m-%d %H:%M:%S"
_EVENT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (CONNECT|DISCONNECT) \| "
    r"client=(\S+) \| (.*?) \| total=\d+\s*$")
_SERVER = re.compile(r"^SERVIDOR INICIADO!!\s*\| (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$")


@dataclass(frozen=True)
class LogEvent:
    ts: float
    kind: str            # CONNECT | DISCONNECT | SERVER_START
    client: Optional[str]
    detail: str


def parse_line(line: str) -> Optional[LogEvent]:
    m = _EVENT.match(line)
    if m:
        ts = datetime.strptime(m.group(1), _TS).timestamp()
        return LogEvent(ts, m.group(2), m.group(3), m.group(4))
    m = _SERVER.match(line)
    if m:
        ts = datetime.strptime(m.group(1), _TS).timestamp()
        return LogEvent(ts, "SERVER_START", None, "")
    return None


class LogFollower:
    def __init__(self, path: Path, on_event: Callable[[LogEvent], None]):
        self._path = Path(path)
        self._on_event = on_event
        self._offset = 0

    def poll(self) -> int:
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return 0
        if size < self._offset:          # truncado/rotacionado
            self._offset = 0
        if size == self._offset:
            return 0
        n = 0
        # newline="" preserva o \r\n do arquivo real (escrito no Windows),
        # mantendo o offset em bytes correto.
        with open(self._path, "r", encoding="utf-8", errors="replace",
                  newline="") as f:
            f.seek(self._offset)
            for line in f:
                if not line.endswith(("\n", "\r\n")):
                    break                 # linha ainda sendo escrita
                ev = parse_line(line.rstrip("\r\n"))
                if ev:
                    self._on_event(ev)
                    n += 1
                self._offset += len(line.encode("utf-8"))
        return n
