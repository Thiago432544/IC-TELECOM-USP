"""Serie temporal e eventos em SQLite (WAL), thread-safe por lock."""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples(ts REAL, origin TEXT, metric TEXT, value REAL);
CREATE INDEX IF NOT EXISTS ix_samples ON samples(origin, metric, ts);
CREATE TABLE IF NOT EXISTS events(ts REAL, origin TEXT, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_events ON events(origin, kind, ts);
CREATE TABLE IF NOT EXISTS samples_hourly(
  hour_ts REAL, origin TEXT, metric TEXT,
  n INTEGER, mean REAL, min REAL, max REAL,
  PRIMARY KEY(hour_ts, origin, metric));
"""


class Store:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def add_sample(self, ts, origin, metric, value):
        with self._lock:
            self._conn.execute("INSERT INTO samples VALUES(?,?,?,?)",
                               (ts, origin, metric, value))
            self._conn.commit()

    def add_event(self, ts, origin, kind, detail):
        with self._lock:
            self._conn.execute("INSERT INTO events VALUES(?,?,?,?)",
                               (ts, origin, kind, detail))
            self._conn.commit()

    def samples(self, origin, metric, since, until=None):
        q = "SELECT ts, value FROM samples WHERE origin=? AND metric=? AND ts>=?"
        args = [origin, metric, since]
        if until is not None:
            q += " AND ts<=?"
            args.append(until)
        with self._lock:
            return self._conn.execute(q + " ORDER BY ts", args).fetchall()

    def last_sample(self, origin, metric):
        with self._lock:
            row = self._conn.execute(
                "SELECT ts, value FROM samples WHERE origin=? AND metric=?"
                " ORDER BY ts DESC LIMIT 1", (origin, metric)).fetchone()
        return row

    def events(self, since, kind=None, origin=None):
        q = "SELECT ts, origin, kind, detail FROM events WHERE ts>=?"
        args = [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if origin:
            q += " AND origin=?"
            args.append(origin)
        with self._lock:
            return self._conn.execute(q + " ORDER BY ts", args).fetchall()

    def count_events(self, origin, kind, since):
        with self._lock:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE origin=? AND kind=? AND ts>=?",
                (origin, kind, since)).fetchone()
        return n

    def purge(self, now, raw_days=90):
        cutoff = now - raw_days * 86400
        with self._lock:
            self._conn.execute("""
                INSERT OR REPLACE INTO samples_hourly
                SELECT CAST(ts/3600 AS INTEGER)*3600.0, origin, metric,
                       COUNT(*), AVG(value), MIN(value), MAX(value)
                FROM samples WHERE ts < ?
                GROUP BY CAST(ts/3600 AS INTEGER), origin, metric""", (cutoff,))
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
