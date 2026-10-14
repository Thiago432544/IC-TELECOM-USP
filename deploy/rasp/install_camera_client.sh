#!/bin/bash
# Instala o cliente de camera como servico systemd + NTP interno.
# Uso: sudo bash install_camera_client.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT=/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/2026_02_05_Cliente_H01.py

[ -f "$CLIENT" ] || { echo "ERRO: $CLIENT nao existe nesta Rasp"; exit 1; }

PRINCIPAL="$(dirname "$CLIENT")"

# Sob systemd nao ha sessao grafica: o watchdog_test.py original abre o Chrome
# sem --headless e falharia sempre - e, falhando, mata o cliente (F7). O do
# repositorio e' headless e blindado. O original fica guardado ao lado.
echo "== watchdog_test.py (blindado, headless, com hora no router.log) =="
if [ -f "$PRINCIPAL/watchdog_test.py" ] && ! grep -q "Nunca levanta excecao" "$PRINCIPAL/watchdog_test.py"; then
    cp -p "$PRINCIPAL/watchdog_test.py" "$PRINCIPAL/watchdog_test.py.original-$(date +%Y%m%d%H%M%S)"
fi
install -o pi -g pi -m 0644 "$HERE/watchdog_test.py" "$PRINCIPAL/watchdog_test.py"
if [ ! -f "$PRINCIPAL/router.conf" ] && ip route show default | grep -q "192.168.11.1 "; then
    echo "AVISO: o gateway e' 192.168.11.1 (Teltonika). Crie $PRINCIPAL/router.conf"
    echo "       com modelo/usuario/senha antes de iniciar o servico (ver watchdog_test.py)."
fi

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
echo "     e no PC do SPA: CONNECT da camera no LOG_connections.txt em ate ~20 s"
echo "  4. confira o relogio: timedatectl   (Synchronized deve virar yes em ~1 min)"
