# -*- coding: utf-8 -*-
"""
Created on Sun Jan  4 03:11:00 2026

@author: CILIP
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
                header = client_socket.recv(4)
                if not header or len(header) < 4:
                    reason = f"[{client_id}] Header invalido, encerrando conexao"
                    print(reason)
                    status=False
                    break
                    
                file_size = int.from_bytes(header, "big")

                # ===== RECEBE NOME =====
                name_len = client_socket.recv(1)
                if not name_len:
                    reason = f"[{client_id}] Nome invalido, encerrando conexao"
                    print(reason)
                    status=False
                    break
                    
                name_len = name_len[0]

                file_name = client_socket.recv(name_len)
                if not file_name or len(file_name) < name_len:
                    reason = f"[{client_id}] Nome incompleto, encerrando conexao"
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
                print(f"[{client_id}] Δt = {delta:.3f}s | Frame recebido: {file_name}")

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
    
        print(f"🔴 Cliente {client_id} desconectado | Total: {total}")
    
        client_socket.close()
    
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"{timestamp} | DISCONNECT | client={client_id} | {reason} | total={total}\n"
    
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(msg)
    

# ==============================
# ACEITA CONEXÕES
# ==============================
def handle_client(client_socket, client_address):
    global clientes
    try:
        id_len = int.from_bytes(client_socket.recv(1), "big")
        client_id = client_socket.recv(id_len).decode()

        with lock:
            clientes += 1
            client_status[client_id] = {
                "ip": client_address[0],
                "connected": True,
                "last_update": time.time()
            }

        print(f"🟢 Cliente {client_id} conectado | IP: {client_address[0]} | Total: {clientes}")
        
        #LOG
        msg = f"CONNECT | client={client_id} | IP: {client_address[0]} | total={clientes}"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} | {msg}\n"
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(line)
        
        
        t = threading.Thread(target=client_worker, args=(client_socket, client_id), daemon=True)
        t.start()

    except Exception as err:
        print("Erro ao iniciar cliente:", err)
        client_socket.close()


# ==============================
# MAIN SERVER
# ==============================
def start_server():
    print("🚀 Servidor Iniciado!")
    msg = f"SERVIDOR INICIADO!!  "
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{msg} | {timestamp}\n"
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line)
        
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    print("Aguardando conexão")
    srv.listen()

    while True:
        try:
            sock, addr = srv.accept()
            handle_client(sock, addr)
        except:
            break

    srv.close()


if __name__ == "__main__":
    start_server()