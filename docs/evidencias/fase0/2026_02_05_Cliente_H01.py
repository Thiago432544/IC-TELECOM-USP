import cv2

from picamera2 import Picamera2

from datetime import datetime

import socket

import time

import subprocess

import threading

import os 

import statistics

from collections import deque

import watchdog_test



# ==============================

wait_time = 90

HOST = '192.168.10.101'

PORT = 55000

FRAME_SIZE = (1280, 720)

JPEG_QUALITY = 90

CAPTURE_INTERVAL = 1  # segundos

SENSOR_SAMPLE_INTERVAL = 0.1

SENSOR_HISTORY_SECONDS = 300

SENSOR_HISTORY_MAXLEN = int(SENSOR_HISTORY_SECONDS / SENSOR_SAMPLE_INTERVAL)

LOGS_FAILURE_DIR = os.path.expanduser("~/Desktop/logs_failure")

# ==============================



# ---------- Câmera ----------

piCam = Picamera2()

piCam.preview_configuration.main.size = FRAME_SIZE

piCam.preview_configuration.main.format = "RGB888"

piCam.preview_configuration.align()

piCam.configure("preview")

piCam.start()



your_ip = subprocess.getoutput("hostname -I").split()[0]

CLIENT_ID = your_ip.strip().split(".")[-1]



temperature = "temp=?"



voltage = "volt=?"



sensor_history = deque(maxlen=SENSOR_HISTORY_MAXLEN)



sensor_lock = threading.Lock()



def parse_temp_celsius(raw):

    try:

        return float(raw.split('=')[1].split("'")[0])

    except: 

        return -1

        

def parse_volts(raw):

    try:

        return float(raw.split('=')[1].rstrip('V'))



    except:



        return None





# ---------- Thread temperatura ----------

def update_temperature():

    global temperature,voltage

    while True:

        try:

            temperature = subprocess.getoutput(

                "vcgencmd measure_temp"

            ).strip()

        except:

            temperature = "temp=?"

        

        try:

            voltage = subprocess.getoutput(

                "vcgencmd measure_volts core"

            ).strip()



        except:

            voltage = "volt=?"



        temp_value = parse_temp_celsius(temperature)



        volt_value = parse_volts(voltage)



        if temp_value is not None and volt_value is not None:



            with sensor_lock:



                sensor_history.append((datetime.now(), temp_value, volt_value))



        time.sleep(SENSOR_SAMPLE_INTERVAL)



threading.Thread(

    target=update_temperature,

    daemon=True

).start()



# ---------- Log de falha ----------



def _stats_lines(label, unit, values):

    try:

        mode_value = statistics.mode(values)

    except statistics.StatisticsError:

        mode_value = None

    stdev_value = statistics.stdev(values) if len(values) > 1 else 0.0

    moda_str = f"{mode_value:.3f} {unit}" if mode_value is not None else "N/A"

    return [

        f"--- {label} ---",

        f"Amostras: {len(values)}",

        f"Pico (máximo): {max(values):.3f} {unit}",

        f"Mínimo: {min(values):.3f} {unit}",

        f"Média: {statistics.mean(values):.3f} {unit}",

        f"Mediana: {statistics.median(values):.3f} {unit}",

        f"Moda: {moda_str}",

        f"Desvio padrão: {stdev_value:.3f} {unit}",

        "",



    ]







def log_failure(reason):

    os.makedirs(LOGS_FAILURE_DIR, exist_ok=True)

    with sensor_lock:

        history_snapshot = list(sensor_history)

    now = datetime.now()

    log_path = os.path.join(

        LOGS_FAILURE_DIR,

        f"falha_{now.strftime('%Y-%m-%d_%H-%M-%S')}.txt"

    )



    lines = [

        f"Data/Hora da falha: {now.strftime('%Y-%m-%d %H:%M:%S')}",

        f"Motivo: {reason}",

        f"Janela de amostras: últimos {SENSOR_HISTORY_SECONDS}s (ou desde o início, se menor)",

        "",

    ]



    if history_snapshot:

        temps = [t for _, t, _ in history_snapshot]

        volts = [v for _, _, v in history_snapshot]

        lines += _stats_lines("Temperatura", "°C", temps)

        lines += _stats_lines("Tensão (core)", "V", volts)

    else:

        lines.append("Nenhuma amostra de sensor disponível no momento da falha.")

    with open(log_path, "w", encoding="utf-8") as f:

        f.write("\n".join(lines))

    print(f"[LOG] Falha registrada em {log_path}")





# ---------- Captura + envio ----------

def capture_and_send(client_socket):

    while True:

        try:

            frame = piCam.capture_array()



            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            text = f"{timestamp} | {your_ip} | {temperature}"



            cv2.putText(

                frame, text, (10, 50),

                cv2.FONT_HERSHEY_DUPLEX,

                1.0, (255, 255, 0), 2, cv2.LINE_AA

            )



            ret, encoded = cv2.imencode(

                '.jpg', frame,

                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]

            )

            if not ret:

                continue



            data = encoded.tobytes()

            file_name = "frame.jpg"



            # protocolo

            client_socket.sendall(len(data).to_bytes(4, 'big'))

            client_socket.sendall(len(file_name).to_bytes(1, 'big'))

            client_socket.sendall(file_name.encode('utf-8'))

            client_socket.sendall(data)



            print(f"Frame enviado ({len(data)/1024:.1f} KB)")

            time.sleep(CAPTURE_INTERVAL)



        except Exception as e:

            print(f"Erro no envio: {e}")

            log_failure(f"erro no envio: {e}")

            print(f"Aguarda 10 segundos")

            time.sleep(10)

            break



# ---------- Main ----------

def main():

    global wait_time

    while True:

        try:

            print(f"Tentando conectar ao servidor {HOST}:{PORT}...")

            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            client_socket.settimeout(6)

            client_socket.connect((HOST, PORT))



            client_socket.sendall(len(CLIENT_ID).to_bytes(1, 'big'))

            client_socket.sendall(CLIENT_ID.encode('utf-8'))



            print("Conectado ao servidor. Iniciando transmissão...\n")

            capture_and_send(client_socket)

            wait_time = 80



        except KeyboardInterrupt:

            print("Encerrando cliente...")

            break

        except Exception as e:

            print(f"Erro: {e}. Tentando novamente em {wait_time}s...")

            watchdog_test.restart_router()

            time.sleep(wait_time)

            wait_time = min(wait_time*2,1800)

        finally:

            try:

                client_socket.close()

            except:

                pass



if __name__ == "__main__":

    main()

