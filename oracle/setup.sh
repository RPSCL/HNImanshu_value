#!/usr/bin/env bash
# oracle/setup.sh — one-time setup of the live-price server (Oracle Linux, user opc). Safe to run again.
#   curl -fsSL https://raw.githubusercontent.com/RPSCL/HNImanshu_value/main/oracle/setup.sh -o setup.sh && bash setup.sh
set -euo pipefail

echo "== time zone: India"
sudo timedatectl set-timezone Asia/Kolkata

echo "== packages: git + Python 3.11"
sudo dnf install -y git python3.11 python3.11-pip

echo "== code: /opt/hni (the GitHub repo; later updates: cd /opt/hni && git pull)"
sudo mkdir -p /opt/hni /opt/hni-secrets
sudo chown opc:opc /opt/hni /opt/hni-secrets
chmod 700 /opt/hni-secrets
if [ -d /opt/hni/.git ]; then git -C /opt/hni pull --ff-only; else git clone https://github.com/RPSCL/HNImanshu_value.git /opt/hni; fi

echo "== Python packages (own environment in /opt/hni/venv)"
python3.11 -m venv /opt/hni/venv
/opt/hni/venv/bin/pip install --upgrade pip
/opt/hni/venv/bin/pip install firebase-admin smartapi-python pyotp logzero websocket-client requests pandas

echo "== secrets file (fill it in once): /opt/hni-secrets/hni.env"
if [ ! -f /opt/hni-secrets/hni.env ]; then
  cat > /opt/hni-secrets/hni.env <<'ENV'
ANGEL_API_KEY=
ANGEL_CLIENT_CODE=
ANGEL_PIN=
ANGEL_TOTP_SECRET=
ENV
fi
chmod 600 /opt/hni-secrets/hni.env
[ -f /opt/hni-secrets/sa.json ] && chmod 600 /opt/hni-secrets/sa.json || echo "   (still to do: copy the Firebase service-account key to /opt/hni-secrets/sa.json)"

echo "== service"
sudo cp /opt/hni/oracle/hni-ltp.service /etc/systemd/system/hni-ltp.service
sudo systemctl daemon-reload
echo
echo "Next: fill /opt/hni-secrets/hni.env, copy sa.json, test once, then:  sudo systemctl enable --now hni-ltp"
