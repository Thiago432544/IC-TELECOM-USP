#!/bin/bash
# Instala a caixa-preta numa Rasp de camera. NAO mexe no cliente: so observa.
#
# Uso (na Rasp, dentro da pasta caixa-preta/):
#   sudo bash instalar_caixa_preta.sh 102
#   sudo bash instalar_caixa_preta.sh 105            # gateway detectado sozinho
#
# Pode ser rodado de novo sem estrago: atualiza o script e reinicia o servico,
# e o estado (onde parou de ler o router.log) e os registros sao preservados.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CAM="${1:-}"

[ "$(id -u)" -eq 0 ] || { echo "ERRO: rode com sudo"; exit 1; }
[[ "$CAM" =~ ^[0-9]{3}$ ]] || { echo "ERRO: informe a camera, ex.: sudo bash $0 102"; exit 1; }
for f in caixa_preta.py caixa-preta.service journald-persistente.conf; do
    [ -f "$HERE/$f" ] || { echo "ERRO: falta $f ao lado do instalador"; exit 1; }
done

# O gateway e' a LAN do proprio CPE: .254 na Elsys, .1 no Teltonika.
GW="$(ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}')"
[ -n "$GW" ] || GW=192.168.11.254
echo "== camera $CAM, gateway $GW =="

echo "== 1. journal persistente =="
mkdir -p /var/log/journal /etc/systemd/journald.conf.d
cp "$HERE/journald-persistente.conf" /etc/systemd/journald.conf.d/caixa-preta.conf
systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
systemctl restart systemd-journald
journalctl --list-boots 2>/dev/null | tail -3 || true

echo "== 2. agente =="
mkdir -p /opt/caixa-preta /var/log/caixa-preta /var/lib/caixa-preta
install -m 0755 "$HERE/caixa_preta.py" /opt/caixa-preta/caixa_preta.py
if [ -f /etc/caixa-preta.conf ]; then
    cp /etc/caixa-preta.conf "/etc/caixa-preta.conf.$(date +%s).bak"
fi
cat > /etc/caixa-preta.conf <<EOF
# Gerado por instalar_caixa_preta.sh. Chaves validas: ver PADRAO em caixa_preta.py
camera = $CAM
gateway = $GW
EOF

echo "== 3. servico =="
cp "$HERE/caixa-preta.service" /etc/systemd/system/caixa-preta.service
systemctl daemon-reload
systemctl enable caixa-preta.service >/dev/null
systemctl restart caixa-preta.service

echo "== 4. conferindo (40 s) =="
sleep 40
systemctl is-active caixa-preta.service
python3 /opt/caixa-preta/caixa_preta.py --relatorio --horas 1 | tail -15

cat <<'EOF'

PRONTO. O que foi instalado:
  /opt/caixa-preta/caixa_preta.py          o agente
  /etc/caixa-preta.conf                    camera e gateway
  /var/log/caixa-preta/*.jsonl             os registros
  /etc/systemd/journald.conf.d/            journal persistente

Ler depois de uma queda:
  python3 /opt/caixa-preta/caixa_preta.py --relatorio            # tudo
  python3 /opt/caixa-preta/caixa_preta.py --relatorio --horas 6  # ultimas 6 h
  journalctl -u caixa-preta --since "-6h"
  journalctl -k --since "-6h"                                    # kernel

Copiar para analise:  tar czf ~/caixa-preta-$(hostname).tgz /var/log/caixa-preta
EOF
