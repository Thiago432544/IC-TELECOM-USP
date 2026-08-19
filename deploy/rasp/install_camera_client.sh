#!/bin/bash
# Instala o cliente de camera como servico systemd + NTP interno.
# Uso: sudo bash install_camera_client.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT=/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/2026_02_05_Cliente_H01.py

[ -f "$CLIENT" ] || { echo "ERRO: $CLIENT nao existe nesta Rasp"; exit 1; }

echo "== unit systemd =="
cp "$HERE/camera-client.service" /etc/systemd/system/camera-client.service
systemctl daemon-reload
systemctl enable camera-client.service

echo "== NTP (systemd-timesyncd -> PC do SPA) =="
mkdir -p /etc/systemd/timesyncd.conf.d
cp "$HERE/porto-ntp.conf" /etc/systemd/timesyncd.conf.d/porto-ntp.conf
timedatectl set-ntp true
systemctl restart systemd-timesyncd

echo
echo "PRONTO. Agora, NA MAO (para nao matar uma transmissao em andamento):"
echo "  1. pare o cliente que roda em sessao manual (Ctrl+C no terminal/VNC)"
echo "  2. sudo systemctl start camera-client"
echo "  3. confira: systemctl status camera-client ; journalctl -u camera-client -f"
echo "  4. confira o relogio: timedatectl   (Synchronized deve virar yes em ~1 min)"
