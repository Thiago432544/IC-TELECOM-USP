#!/bin/bash
# Instala o auto-reparo do caminho de rede numa Rasp de camera.
#
# Uso (na Rasp, dentro da pasta auto-reparo/, com a pasta caixa-preta/ ao lado):
#   sudo bash instalar_auto_reparo.sh 102              # ja agindo
#   sudo bash instalar_auto_reparo.sh 102 --observar   # so registra o que faria
#
# Trocar de modo depois: editar "modo" em /etc/auto-reparo.conf e
#   sudo systemctl restart auto-reparo
# Suspender (manutencao):  sudo touch /etc/auto-reparo.pausa
# Retomar:                 sudo rm /etc/auto-reparo.pausa
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CAM="${1:-}"
MODO=ativo
[ "${2:-}" = "--observar" ] && MODO=observar

[ "$(id -u)" -eq 0 ] || { echo "ERRO: rode com sudo"; exit 1; }
[[ "$CAM" =~ ^[0-9]{3}$ ]] || { echo "ERRO: informe a camera, ex.: sudo bash $0 102"; exit 1; }
# o auto-reparo usa funcoes da caixa-preta, que mora na pasta irma
CP="$HERE/../caixa-preta/caixa_preta.py"
[ -f "$CP" ] || CP="$HERE/caixa_preta.py"
for f in "$HERE/auto_reparo.py" "$HERE/auto-reparo.service" "$CP"; do
    [ -f "$f" ] || { echo "ERRO: falta $f (a pasta caixa-preta/ precisa estar ao lado)"; exit 1; }
done
if grep -q $'\r' "$HERE/auto_reparo.py" "$HERE/auto-reparo.service" "$CP"; then
    echo "ERRO: arquivos com fim de linha do Windows. Rode antes:"
    echo "  sed -i 's/\\r\$//' ../*/*.sh ../*/*.py ../*/*.service ../*/*.conf"
    exit 1
fi

GW="$(ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}')"
IFACE="$(ip route show default 2>/dev/null | awk '/^default/ {for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
[ -n "$GW" ] || GW=192.168.11.254
[ -n "$IFACE" ] || IFACE=eth0
CON="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null \
       | awk -F: -v d="$IFACE" '$NF==d {sub(":" d "$", ""); gsub(/\\:/, ":"); print; exit}' || true)"
echo "== camera $CAM | modo $MODO | gateway $GW | interface $IFACE | conexao '${CON:-?}' =="
[ -n "$CON" ] || echo "AVISO: conexao do NetworkManager nao encontrada; o degrau 1 vai ser pulado."

echo "== 1. arquivos =="
mkdir -p /opt/caixa-preta /var/log/caixa-preta /var/lib/caixa-preta
install -m 0755 "$HERE/auto_reparo.py" /opt/caixa-preta/auto_reparo.py
# o auto-reparo usa funcoes da caixa-preta: mesma versao nos dois
if ! cmp -s "$CP" /opt/caixa-preta/caixa_preta.py 2>/dev/null; then
    install -m 0755 "$CP" /opt/caixa-preta/caixa_preta.py
    systemctl try-restart caixa-preta.service 2>/dev/null || true
    echo "caixa_preta.py atualizado"
fi
if [ -f /etc/auto-reparo.conf ]; then
    cp /etc/auto-reparo.conf "/etc/auto-reparo.conf.$(date +%s).bak"
fi
cat > /etc/auto-reparo.conf <<EOF
# Gerado por instalar_auto_reparo.sh. Chaves validas: ver PADRAO em auto_reparo.py
camera = $CAM
modo = $MODO
gateway = $GW
iface = $IFACE
conexao_nm = $CON
EOF

echo "== 2. servico =="
cp "$HERE/auto-reparo.service" /etc/systemd/system/auto-reparo.service
systemctl daemon-reload
systemctl enable auto-reparo.service >/dev/null
systemctl restart auto-reparo.service

echo "== 3. conferindo (75 s: um teste do caminho por minuto) =="
sleep 75
systemctl is-active auto-reparo.service
journalctl -u auto-reparo --since "-2min" --no-pager -o cat | tail -5

cat <<EOF

PRONTO (modo $MODO). Sem queda, o journal so mostra "auto_reparo_inicio":
ele so registra quando o caminho ate o PC do SPA cai.

  journalctl -u auto-reparo -f                                  # ao vivo
  python3 /opt/caixa-preta/caixa_preta.py --relatorio --horas 6  # junto com a caixa-preta
  sudo touch /etc/auto-reparo.pausa                             # suspender
EOF
