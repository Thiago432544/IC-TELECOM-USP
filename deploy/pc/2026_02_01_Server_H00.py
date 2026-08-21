# -*- coding: utf-8 -*-
r"""
Created on Sun Jan  4 03:11:00 2026

@author: CILIP

EM PRODUCAO: C:\Users\CILIP\Documents\2026_02_01_Server_H00.py, no PC do SPA.
Este arquivo tem o mesmo nome de proposito. Em 19/08/2026 o patch do timeout foi
aplicado a um arquivo com OUTRO nome, e o servidor rodou mais um dia inteiro com
o bug - ate 20/08 as 20:53. Confirme o alvo pelo processo em execucao, nunca
pelo nome do arquivo nem pelo titulo da janela:

    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Select-Object ProcessId, CreationDate, CommandLine | Format-List

Procedimento completo: docs/runbooks/fase1-campo.md, passo 3.
"""


import socket
import threading
import os
import time
from datetime import datetime
import cv2
import numpy as np

# ==============================
# CONFIGURAÇÕES
# ==============================
client_status = {}
lock = threading.Lock()
shutdown_flag = False
clientes = 0

SAVE_PATH = r"D:\SPA_Data\Imagens_Porto"
HOST = "192.168.11.101"
PORT = 55000

# Tempo maximo sem receber dados antes de derrubar a conexao.
# Era 1s (15s para a 105): matava conexoes vivas porem lentas -> Broken pipe
# na camera -> ~588 reconexoes/dia na 106 (diagnostico de 18-19/08/2026).
SOCKET_TIMEOUT = 30

LOG_TXT = r"D:\SPA_Data\LOG_connections.txt"
def say(msg):
    """Escreve no console sem nunca poder derrubar a conexao. O print do emoji
    de desconexao roda dentro do `finally` do client_worker, antes de fechar o
    socket e gravar a linha DISCONNECT: num console em cp1252 ele estourava
    UnicodeEncodeError e levava os dois junto - queda invisivel no log."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
def recv_exact(sock, n):
    """Le exatamente n bytes. recv() pode devolver MENOS do que o pedido: numa
    LTE ruim o header de 4 bytes chegava partido e o servidor derrubava a
    conexao como "Header invalido" (F12, diagnostico de 20/08/2026).
    Devolve b"" se o cliente fechou no limite do frame, ou o pedaco parcial se
    o stream foi truncado no meio - quem chama distingue os dois casos."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def prepare_socket(sock):
    """Timeout e keepalive em toda conexao aceita. Sem isso um socket
    meio-aberto - CPE reiniciando, camera em brownout - fica pendurado para
    sempre e nunca e detectado (F11)."""
    sock.settimeout(SOCKET_TIMEOUT)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

# ==============================
# THREAD ÚNICA PARA CADA CLIENTE
# ==============================
def client_worker(client_socket, client_id):
    global clientes
    today = datetime.today().strftime("%Y_%m_%d")
    daily_path = os.path.join(SAVE_PATH, today, client_id)
    os.makedirs(daily_path, exist_ok=True)

    save_every = 1 if client_id == "105" else 10  # 105 grava todo frame; 102/106, 1 a cada 10
    
    counter = 0
    last_timestamp = None
    status = True
    try:
        client_socket.settimeout(SOCKET_TIMEOUT)
        reason = ""
        while status==True:
            try:
                # ===== RECEBE HEADER =====
                header = recv_exact(client_socket, 4)
                if len(header) < 4:
                    reason = (f"[{client_id}] Cliente encerrou a conexao" if not header
                              else f"[{client_id}] Header truncado ({len(header)}/4 bytes)")
                    print(reason)
                    status=False
                    break
                    
                file_size = int.from_bytes(header, "big")

                # ===== RECEBE NOME =====
                name_len = recv_exact(client_socket, 1)
                if not name_len:
                    reason = f"[{client_id}] Nome invalido, encerrando conexao"
                    print(reason)
                    status=False
                    break
                    
                name_len = name_len[0]

                file_name = recv_exact(client_socket, name_len)
                if len(file_name) < name_len:
                    reason = (f"[{client_id}] Cliente encerrou a conexao no nome" if not file_name
                              else f"[{client_id}] Nome truncado ({len(file_name)}/{name_len} bytes)")
                    print(reason)
                    status=False
                    break
                    
                file_name = file_name.decode()

                # ===== RECEBE DADOS =====
                file_data = b""
                while len(file_data) < file_size:
                    chunk = client_socket.recv(min(4096, file_size - len(file_data)))
                    if not chunk:
                        reason = f"[{client_id}] Interrupcao na leitura do frame"
                        print(reason)
                        status=False
                        break
                        
                    file_data += chunk

                if len(file_data) < file_size:
                    reason = f"[{client_id}] Erro no tamanho do arquivo"
                    print(reason)
                    status=False
                    break

                # ===== CALCULAR Δt =====
                recv_time = time.time()
                delta = (recv_time - last_timestamp) if last_timestamp else 0
                last_timestamp = recv_time
                say(f"[{client_id}] Δt = {delta:.3f}s | Frame recebido: {file_name}")

                with lock:
                    client_status[client_id]["last_update"] = recv_time

                # ===== DECODIFICA IMAGEM =====
                image = cv2.imdecode(np.frombuffer(file_data, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    reason = f"[{client_id}] Erro ao decodificar imagem"
                    print(reason)
                    break

                # ===== GRAVAÇÃO PERIÓDICA =====
                today = datetime.today().strftime("%Y_%m_%d")
                daily_path = os.path.join(SAVE_PATH, today, client_id)
                os.makedirs(daily_path, exist_ok=True)
                counter += 1
                if counter >= save_every:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
                    out_file = os.path.join(daily_path, f"{timestamp}_{file_name}")
                    with open(out_file, "wb") as f:
                        f.write(file_data)
                    print(f"[{client_id}] Imagem salva em: {out_file}")
                    counter = 0

            except socket.timeout:
                reason = f"[{client_id}] Timeout, encerrando conexao..."
                print(reason)
                status=False
                break
                
                #continue
            except Exception as e:
                reason = f"[{client_id}] Problema no frame: {e}"
                print(reason)
                status=False
                break   

    except Exception as e:
        reason = f"[{client_id}] Problema no frame: {e}"
        print(reason)
        status=False
        
    finally:
        with lock:
            clientes -= 1
            total = clientes
    
        say(f"🔴 Cliente {client_id} desconectado | Total: {total}")
    
        client_socket.close()
    
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"{timestamp} | DISCONNECT | client={client_id} | {reason} | total={total}\n"
    
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(msg)
    

# ==============================
# ACEITA CONEXÕES
# ==============================
def handle_client(client_socket, client_address):
    # O handshake do ID NAO pode rodar aqui: esta funcao e chamada pela thread
    # do accept(). Um cliente que completa o TCP e nunca envia o ID congelava o
    # servidor inteiro - 5h21min sem aceitar ninguem em 20/08/2026 (F11).
    t = threading.Thread(target=client_session,
                         args=(client_socket, client_address), daemon=True)
    t.start()


def client_session(client_socket, client_address):
    global clientes
    try:
        prepare_socket(client_socket)

        id_len = recv_exact(client_socket, 1)
        if not id_len:
            client_socket.close()
            return

        raw_id = recv_exact(client_socket, id_len[0])
        if len(raw_id) < id_len[0]:
            print(f"Handshake truncado de {client_address[0]}, descartando")
            client_socket.close()
            return
        client_id = raw_id.decode()

        with lock:
            clientes += 1
            client_status[client_id] = {
                "ip": client_address[0],
                "connected": True,
                "last_update": time.time()
            }

        say(f"\U0001f7e2 Cliente {client_id} conectado | IP: {client_address[0]} | Total: {clientes}")

        #LOG
        msg = f"CONNECT | client={client_id} | IP: {client_address[0]} | total={clientes}"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} | {msg}\n"
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(line)

        client_worker(client_socket, client_id)

    except Exception as err:
        print("Erro ao iniciar cliente:", err)
        client_socket.close()

# ==============================
# MAIN SERVER
# ==============================
def serve_forever(srv):
    while True:
        try:
            sock, addr = srv.accept()
        except (ConnectionResetError, ConnectionAbortedError):
            continue          # conexao morreu antes do accept: nao mata o servidor
        except OSError:
            break             # listener fechado: encerra de verdade
        try:
            handle_client(sock, addr)
        except Exception as err:
            print("Erro ao aceitar conexao:", err)
            try:
                sock.close()
            except Exception:
                pass

def start_server():
    say("🚀 Servidor Iniciado!")
    msg = f"SERVIDOR INICIADO!!  "
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{msg} | {timestamp}\n"
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line)
        
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    say("Aguardando conexão")
    srv.listen()

    serve_forever(srv)

    srv.close()


if __name__ == "__main__":
    start_server()