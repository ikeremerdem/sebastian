#!/usr/bin/env bash
# One-time (idempotent) host setup for Sebastian. Run as root from the checkout:
#   sudo /opt/sebastian/deploy/install.sh
# Creates the service user and directories, installs the systemd units and a narrow
# sudoers rule for deploys, and writes /etc/sebastian/env if it does not exist yet.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi

APP_DIR=${APP_DIR:-/opt/sebastian}
DATA_DIR=${DATA_DIR:-/var/lib/sebastian}
ENV_DIR=/etc/sebastian
SRC="$APP_DIR/deploy"

id sebastian &>/dev/null || useradd --system --create-home --home-dir "$DATA_DIR" --shell /bin/bash sebastian
install -d -o sebastian -g sebastian -m 750 "$DATA_DIR" "$DATA_DIR/backups"
install -d -o sebastian -g sebastian "$APP_DIR"
install -d -o root -g sebastian -m 750 "$ENV_DIR"

if [[ ! -f $ENV_DIR/env ]]; then
  api_key=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  session_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  sed -e "s|__API_KEY__|$api_key|" -e "s|__SESSION_SECRET__|$session_secret|" "$SRC/env.example" > "$ENV_DIR/env"
  chown root:sebastian "$ENV_DIR/env"; chmod 640 "$ENV_DIR/env"
  echo "Created $ENV_DIR/env with a fresh API key and session secret. Review the timezone!"
else
  echo "$ENV_DIR/env already exists; left untouched."
fi

install -m 644 "$SRC/sebastian.service" /etc/systemd/system/sebastian.service
install -m 644 "$SRC/sebastian-backup.service" /etc/systemd/system/sebastian-backup.service
install -m 644 "$SRC/sebastian-backup.timer" /etc/systemd/system/sebastian-backup.timer

# deploy.sh runs as the sebastian user and may only restart/inspect its own service.
cat > /etc/sudoers.d/sebastian <<'SUDOERS'
sebastian ALL=(root) NOPASSWD: /usr/bin/systemctl restart sebastian, /usr/bin/systemctl status sebastian
SUDOERS
chmod 440 /etc/sudoers.d/sebastian
visudo -cf /etc/sudoers.d/sebastian >/dev/null

systemctl daemon-reload
systemctl enable sebastian.service sebastian-backup.timer >/dev/null
systemctl start sebastian-backup.timer
echo "Installed. Next: deploy a release with  sudo -u sebastian $APP_DIR/deploy/deploy.sh latest"
