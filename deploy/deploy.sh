#!/usr/bin/env bash
# Deploy a tagged release without touching data.
#
#   deploy.sh v0.2.0      deploy that tag
#   deploy.sh latest      deploy the highest vX.Y.Z tag
#
# Order: backup -> checkout tag -> uv sync --frozen -> migrate -> restart -> health check.
# If the new version does not become healthy, the previous version is restored.
# Data lives outside the checkout (SEBASTIAN_DATABASE_PATH); this script never deletes it.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/sebastian}
ENV_FILE=${ENV_FILE:-/etc/sebastian/env}
BACKUP_DIR=${BACKUP_DIR:-/var/lib/sebastian/backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-14}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8000/health}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-30}
RESTART_CMD=${RESTART_CMD:-sudo systemctl restart sebastian}
UV=${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}

log() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: deploy.sh <tag|latest>"
cd "$APP_DIR"
[[ -x $UV ]] || die "uv not found (set UV=/path/to/uv)"
[[ -f $ENV_FILE ]] && { set -a; . "$ENV_FILE"; set +a; }

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  while (( SECONDS < deadline )); do
    if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then return 0; fi
    sleep 1
  done
  return 1
}

sync_env() { "$UV" sync --frozen --no-dev --quiet; }

previous=$(git describe --tags --exact-match 2>/dev/null || git rev-parse HEAD)
log "current version: $previous"

log "fetching tags"
git fetch --tags --prune --force origin
if [[ $1 == latest ]]; then
  target=$(git tag --list 'v[0-9]*' --sort=-v:refname | head -n1)
  [[ -n $target ]] || die "no vX.Y.Z tags found"
else
  target=$1
fi
git rev-parse -q --verify "refs/tags/$target^{commit}" >/dev/null || die "tag '$target' not found"

if [[ $previous == "$target" ]]; then log "already on $target; re-running sync, migrate and health check"; fi

db=${SEBASTIAN_DATABASE_PATH:-}
backup_file=""
if [[ -n $db && -f $db && -x $APP_DIR/.venv/bin/sebastian ]]; then
  log "backing up database"
  backup_file=$("$APP_DIR/.venv/bin/sebastian" backup --to "$BACKUP_DIR" --keep "$KEEP_BACKUPS")
  echo "    $backup_file"
else
  log "no existing database/venv yet; skipping backup (first deploy)"
fi

rollback() {
  printf '\033[1;31m==> deploy of %s failed; rolling back to %s\033[0m\n' "$target" "$previous" >&2
  git checkout --quiet --detach "$previous"
  sync_env || true
  eval "$RESTART_CMD" || true
  if wait_healthy; then
    echo "Rolled back to $previous and healthy." >&2
  else
    echo "Rollback is NOT healthy. Check the service logs." >&2
  fi
  [[ -n $backup_file ]] && echo "Pre-deploy backup (restore only if data looks wrong): $backup_file" >&2
  exit 1
}

log "checking out $target"
git checkout --quiet --detach "$target"

log "installing dependencies (frozen lockfile)"
sync_env || rollback

log "migrating database"
"$APP_DIR/.venv/bin/sebastian" migrate || rollback

log "restarting service"
eval "$RESTART_CMD" || rollback

log "waiting for health"
wait_healthy || rollback

log "deployed $target"
curl -fsS "$HEALTH_URL"; echo
