import shutil
from datetime import datetime
from pathlib import Path
from monitor.taplog import LogFollower, parse_line

FIX = Path("tests/fixtures/log_connections_sample.txt")

def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()

def test_parse_connect():
    ev = parse_line("2026-08-19 10:00:05 | CONNECT | client=102 | IP: 192.168.11.102 | total=1")
    assert ev.kind == "CONNECT" and ev.client == "102"
    assert ev.ts == _ts("2026-08-19 10:00:05")

def test_parse_disconnect_keeps_reason():
    ev = parse_line("2026-08-19 10:01:05 | DISCONNECT | client=106 | [106] Timeout, encerrando conexao... | total=1")
    assert ev.kind == "DISCONNECT" and ev.client == "106"
    assert "Timeout" in ev.detail

def test_parse_server_start_timestamp_at_end():
    ev = parse_line("SERVIDOR INICIADO!!   | 2026-08-19 09:59:00")
    assert ev.kind == "SERVER_START" and ev.client is None
    assert ev.ts == _ts("2026-08-19 09:59:00")

def test_parse_garbage_returns_none():
    assert parse_line("linha corrompida sem formato nenhum") is None
    assert parse_line("") is None

def test_follower_incremental_and_truncation(tmp_path):
    log = tmp_path / "log.txt"
    shutil.copy(FIX, log)
    got = []
    f = LogFollower(log, on_event=got.append)
    assert f.poll() == 5                      # 6 linhas, 1 lixo
    assert f.poll() == 0                      # nada novo
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("2026-08-19 10:05:00 | DISCONNECT | client=102 | [102] x | total=1\n")
    assert f.poll() == 1
    log.write_text("SERVIDOR INICIADO!!   | 2026-08-19 11:00:00\n", encoding="utf-8")
    assert f.poll() == 1                      # truncado -> relido do inicio
    assert got[-1].kind == "SERVER_START"

def test_follower_handles_crlf_like_real_windows_log(tmp_path):
    # o LOG_connections.txt real e escrito em modo texto no Windows -> \r\n
    log = tmp_path / "log.txt"
    line1 = "2026-08-19 10:00:05 | CONNECT | client=102 | IP: 192.168.11.102 | total=1"
    log.write_bytes((line1 + "\r\n").encode("utf-8"))
    got = []
    f = LogFollower(log, on_event=got.append)
    assert f.poll() == 1
    with open(log, "ab") as fh:
        fh.write("2026-08-19 10:00:09 | CONNECT | client=106 | IP: 192.168.11.106 | total=2\r\n".encode("utf-8"))
    assert f.poll() == 1                      # offset em bytes nao pode drifar
    assert got[1].client == "106"
