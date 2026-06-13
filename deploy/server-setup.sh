#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/skud-api"

cd "$APP_DIR"

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if [ ! -f .env ]; then
  cat > .env <<EOF
SKUD_BOOTSTRAP_TOKEN=$(openssl rand -hex 24)
SKUD_QR_SECRET=$(openssl rand -hex 48)
SKUD_UNLOCK_SECONDS=5
EOF
  chmod 600 .env
fi

docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps

echo
echo "API: http://$(hostname -I | awk '{print $1}'):8000"
echo "Admin: http://$(hostname -I | awk '{print $1}'):8000/admin/"
echo "Employee portal: http://$(hostname -I | awk '{print $1}'):8000/employee/"
echo
echo "Bootstrap token is stored in $APP_DIR/.env"
