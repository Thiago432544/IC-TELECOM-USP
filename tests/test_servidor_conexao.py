"""Regressoes dos bugs de conexao do servidor do PC (diagnostico de 20/08/2026).

F11 - o `recv` que le o client_id rodava na thread do accept(), sem timeout e
      sem keepalive. Uma camera que completava o handshake TCP e nao mandava o
      ID congelava o laco inteiro: em 20/08 o servidor ficou 5h21min sem aceitar
      ninguem (a 106 fora, a 102 e a 105 intactas por ja terem thread propria).

F12 - `recv(4)` e `recv(name_len)` sem laco. recv() pode devolver menos que o
      pedido; o servidor tratava isso como "Header invalido" e derrubava a
      conexao. Tambem confundia EOF (cliente foi embora) com stream truncado.
"""
import importlib.util
import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest

SRV = Path("deploy/pc/2026_02_01_Server_H00.py")


@pytest.fixture
def mod(tmp_path):
    # cv2/numpy nao existem na maquina de dev; o modulo so os usa dentro de funcoes
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    sys.modules.setdefault("numpy", types.ModuleType("numpy"))
    spec = importlib.util.spec_from_file_location("servidor_conexao", SRV)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.SAVE_PATH = str(tmp_path / "imagens")
    m.LOG_TXT = str(tmp_path / "LOG_connections.txt")
    return m


# ---------- F12: leitura completa ----------

def test_recv_exact_monta_valor_partido_em_dois_segmentos(mod):
    leitor, escritor = socket.socketpair()
    try:
        def envia():
            escritor.sendall(b"\x00\x02")
            time.sleep(0.05)
            escritor.sendall(b"\x49\xf0")

        threading.Thread(target=envia, daemon=True).start()
        assert mod.recv_exact(leitor, 4) == b"\x00\x02\x49\xf0"
    finally:
        leitor.close()
        escritor.close()


def test_recv_exact_devolve_vazio_quando_cliente_fecha_no_fim_do_frame(mod):
    leitor, escritor = socket.socketpair()
    try:
        escritor.close()
        assert mod.recv_exact(leitor, 4) == b""
    finally:
        leitor.close()


def test_recv_exact_devolve_parcial_quando_o_stream_e_truncado(mod):
    leitor, escritor = socket.socketpair()
    try:
        escritor.sendall(b"\x00\x02")
        escritor.close()
        # parcial != vazio: distingue "cliente foi embora" de "stream truncado"
        assert mod.recv_exact(leitor, 4) == b"\x00\x02"
    finally:
        leitor.close()


# ---------- F11: o accept() nao pode bloquear ----------

def test_prepare_socket_aplica_timeout_e_keepalive(mod):
    a, b = socket.socketpair()
    try:
        mod.prepare_socket(a)
        assert a.gettimeout() == mod.SOCKET_TIMEOUT
        assert a.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
    finally:
        a.close()
        b.close()


def test_cliente_mudo_nao_impede_o_proximo_de_conectar(mod):
    mod.client_status.clear()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    porta = srv.getsockname()[1]
    threading.Thread(target=mod.serve_forever, args=(srv,), daemon=True).start()

    mudo = socket.create_connection(("127.0.0.1", porta))   # conecta e nunca envia o ID
    bom = socket.create_connection(("127.0.0.1", porta))
    bom.sendall(b"\x03" + b"106")
    try:
        prazo = time.time() + 5
        while time.time() < prazo and "106" not in mod.client_status:
            time.sleep(0.05)
        assert "106" in mod.client_status, "o cliente mudo congelou o laco de accept()"
    finally:
        mudo.close()
        bom.close()
        srv.close()


def test_erro_em_uma_conexao_nao_derruba_o_servidor(mod):
    """O `except: break` original encerrava o laco de accept() em silencio na
    primeira excecao - e as threads sao daemon, entao o processo morria junto."""
    mod.client_status.clear()
    original = mod.handle_client
    chamadas = []

    def falha_na_primeira(sock, addr):
        chamadas.append(addr)
        if len(chamadas) == 1:
            raise RuntimeError("falha simulada na primeira conexao")
        return original(sock, addr)

    mod.handle_client = falha_na_primeira

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    porta = srv.getsockname()[1]
    threading.Thread(target=mod.serve_forever, args=(srv,), daemon=True).start()

    ruim = socket.create_connection(("127.0.0.1", porta))
    bom = socket.create_connection(("127.0.0.1", porta))
    bom.sendall(b"\x03" + b"106")
    try:
        prazo = time.time() + 5
        while time.time() < prazo and "106" not in mod.client_status:
            time.sleep(0.05)
        assert "106" in mod.client_status, "uma conexao ruim matou o laco de accept()"
    finally:
        ruim.close()
        bom.close()
        srv.close()


def test_disconnect_e_gravado_mesmo_com_console_que_nao_aceita_emoji(mod):
    """O print do emoji roda dentro do `finally` do client_worker, ANTES de
    client_socket.close() e da linha DISCONNECT. Se o console estiver em cp1252
    ele estoura UnicodeEncodeError e leva os dois junto: socket vazado e queda
    invisivel no log - justo o log de que o monitor depende."""
    def print_cp1252(*args, **kwargs):
        " ".join(str(a) for a in args).encode("cp1252")   # como o console do Windows

    mod.print = print_cp1252
    mod.client_status.clear()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    porta = srv.getsockname()[1]
    threading.Thread(target=mod.serve_forever, args=(srv,), daemon=True).start()

    cam = socket.create_connection(("127.0.0.1", porta))
    cam.sendall(b"\x03" + b"106")
    cam.close()
    try:
        log = Path(mod.LOG_TXT)
        prazo = time.time() + 5
        while time.time() < prazo:
            if log.exists() and "DISCONNECT" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        assert log.exists(), "nem o CONNECT foi gravado"
        assert "DISCONNECT" in log.read_text(encoding="utf-8"), \
            "o print do console derrubou o log de DISCONNECT"
    finally:
        srv.close()
