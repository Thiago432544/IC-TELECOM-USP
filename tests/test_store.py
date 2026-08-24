import sqlite3
from monitor.store import Store

def test_samples_roundtrip(tmp_path):
    s = Store(tmp_path / "m.db")
    s.add_sample(100.0, "102", "frames_min", 6.0)
    s.add_sample(160.0, "102", "frames_min", 5.5)
    s.add_sample(160.0, "106", "frames_min", 2.0)
    assert s.samples("102", "frames_min", since=0) == [(100.0, 6.0), (160.0, 5.5)]
    assert s.last_sample("102", "frames_min") == (160.0, 5.5)
    assert s.last_sample("999", "frames_min") is None
    s.close()

def test_events_and_count(tmp_path):
    s = Store(tmp_path / "m.db")
    for i in range(6):
        s.add_event(100.0 + i, "106", "DISCONNECT", "timeout")
    s.add_event(200.0, "106", "CONNECT", "")
    assert s.count_events("106", "DISCONNECT", since=102.5) == 3
    kinds = [e[2] for e in s.events(since=0, origin="106")]
    assert kinds.count("DISCONNECT") == 6 and kinds.count("CONNECT") == 1
    s.close()

def test_purge_aggregates_old_samples(tmp_path):
    s = Store(tmp_path / "m.db")
    old = 1000.0
    for i in range(10):
        s.add_sample(old + i * 60, "102", "frames_min", float(i))
    now = old + 91 * 86400
    s.add_sample(now, "102", "frames_min", 9.9)
    s.purge(now, raw_days=90)
    assert s.samples("102", "frames_min", since=0) == [(now, 9.9)]
    hourly = s._conn.execute(
        "SELECT n, min, max FROM samples_hourly WHERE origin='102'").fetchall()
    assert hourly == [(10, 0.0, 9.0)]
    s.close()

def test_evento_repetido_nao_duplica(tmp_path):
    s = Store(tmp_path / "m.db")
    for _ in range(3):
        s.add_event(100.0, "106", "DISCONNECT", "timeout")
    assert s.count_events("106", "DISCONNECT", since=0) == 1
    # so' o trio (ts, origin, kind, detail) identico colapsa
    s.add_event(100.0, "106", "DISCONNECT", "outro motivo")
    s.add_event(101.0, "106", "DISCONNECT", "timeout")
    assert s.count_events("106", "DISCONNECT", since=0) == 3
    s.close()

def test_banco_com_duplicata_antiga_e_limpo_ao_abrir(tmp_path):
    # banco gravado pela versao sem indice: o mesmo trecho de log entrou 3x
    db = tmp_path / "m.db"
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE events(ts REAL, origin TEXT, kind TEXT, detail TEXT)")
    for _ in range(3):
        raw.execute("INSERT INTO events VALUES(100.0,'106','DISCONNECT','timeout')")
    raw.execute("INSERT INTO events VALUES(200.0,'106','CONNECT','')")
    raw.commit()
    raw.close()

    s = Store(db)
    assert s.count_events("106", "DISCONNECT", since=0) == 1
    assert s.count_events("106", "CONNECT", since=0) == 1
    s.add_event(100.0, "106", "DISCONNECT", "timeout")
    assert s.count_events("106", "DISCONNECT", since=0) == 1
    s.close()
