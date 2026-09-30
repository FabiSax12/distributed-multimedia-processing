#!/usr/bin/env bash
# Deploy idempotente del coordinador (dmp) en EC2 Amazon Linux 2023.
# - Primera vez: instala dependencias, clona el repo y crea el servicio.
# - Siguientes veces: actualiza a origin/main, sincroniza deps y reinicia.
# Uso:  sudo bash deploy/deploy.sh
#   o:  curl -fsSL https://raw.githubusercontent.com/FabiSax12/distributed-multimedia-processing/main/deploy/deploy.sh | sudo bash
set -euo pipefail

REPO_URL="https://github.com/FabiSax12/distributed-multimedia-processing"
BRANCH="main"
APP_DIR="/opt/dmp"
APP_USER="ec2-user"
SERVICE="dmp-coordinator"
SERVICE_SRC="deploy/coordinator.service"
UV="/home/${APP_USER}/.local/bin/uv"

log() { echo -e "\n==> $*"; }
as_app() { sudo -u "$APP_USER" -H "$@"; }

[[ $EUID -eq 0 ]] || { echo "Ejecuta como root (sudo bash $0)"; exit 1; }
cd /

log "Dependencias del sistema"
command -v git >/dev/null || dnf install -y git

log "uv para ${APP_USER}"
if [[ ! -x "$UV" ]]; then
  as_app bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
as_app "$UV" --version

log "Código fuente en ${APP_DIR}"
mkdir -p "$APP_DIR"
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
if [[ -d "${APP_DIR}/.git" ]]; then
  as_app git -C "$APP_DIR" fetch origin "$BRANCH"
  as_app git -C "$APP_DIR" reset --hard "origin/${BRANCH}"
  as_app git -C "$APP_DIR" clean -fd -e .venv -e .uv-cache -e .env
else
  # Directorio sin repo (o con basura): se vacía y se clona limpio
  find "$APP_DIR" -mindepth 1 -delete
  as_app git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
as_app git -C "$APP_DIR" log -1 --oneline

log "Dependencias Python (coordinator)"
cd "$APP_DIR"
as_app "$UV" sync --package coordinator --frozen

log "Servicio systemd"
install -m 644 "${APP_DIR}/${SERVICE_SRC}" "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

log "Verificación"
for _ in {1..10}; do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 1
done
if systemctl is-active --quiet "$SERVICE"; then
  echo "OK: ${SERVICE} activo"
else
  echo "ERROR: ${SERVICE} no arrancó"
  journalctl -u "$SERVICE" -n 40 --no-pager
  exit 1
fi