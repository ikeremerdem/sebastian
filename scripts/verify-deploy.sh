#!/usr/bin/env bash
# Rehearse the Pi deployment on this machine, in a scratch directory:
#   v0.1.0 first deploy -> seed data -> v0.2.0 (adds a migration) -> v0.3.0 (broken migration -> rollback)
# Asserts that data survives every step and that a failed deploy rolls back to a healthy service.
# Uses a copy of the working tree; your repo, .env and data are never touched.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/sebastian-deploy.XXXXXX")
PORT=${PORT:-18765}
KEY=verify-key
if curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "port $PORT is already in use (a leftover server?). Stop it or set PORT=..." >&2; exit 1
fi
pass() { printf '  \033[32mok\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; echo "scratch dir kept: $T"; exit 1; }
OK=0
cleanup() {
  [[ -f $T/pid ]] && kill "$(cat "$T/pid")" 2>/dev/null || true
  [[ $OK == 1 ]] && rm -rf "$T"
}
trap cleanup EXIT

echo "scratch: $T"
mkdir -p "$T/origin-src" "$T/data"
rsync -a --exclude .venv --exclude .git --exclude data --exclude .env --exclude __pycache__ \
  --exclude .pytest_cache --exclude .ruff_cache "$REPO/" "$T/origin-src/"

git_in() { git -C "$T/origin-src" -c user.name=t -c user.email=t@t "$@"; }
git_in init -q -b main
git_in add -A && git_in commit -qm "v0.1.0" && git_in tag v0.1.0

# v0.2.0: a new additive migration + version bump
cat > "$T/origin-src/src/sebastian/migrations/versions/0002_probe.py" <<'PY'
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("probe", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade() -> None:
    op.drop_table("probe")
PY
sed -i.bak 's/^version = "0.1.0"/version = "0.2.0"/' "$T/origin-src/pyproject.toml" && rm "$T/origin-src/pyproject.toml.bak"
git_in add -A && git_in commit -qm "v0.2.0" && git_in tag v0.2.0

# v0.3.0: a migration that blows up
cat > "$T/origin-src/src/sebastian/migrations/versions/0003_broken.py" <<'PY'
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    raise RuntimeError("deliberately broken migration")


def downgrade() -> None:
    pass
PY
git_in add -A && git_in commit -qm "v0.3.0" && git_in tag v0.3.0

git clone -q "$T/origin-src" "$T/app"

cat > "$T/env" <<ENV
SEBASTIAN_DATABASE_PATH=$T/data/sebastian.db
SEBASTIAN_API_KEY=$KEY
SEBASTIAN_SESSION_SECRET=verify-secret
SEBASTIAN_TIMEZONE=Europe/Berlin
SEBASTIAN_HOST=127.0.0.1
SEBASTIAN_PORT=$PORT
SEBASTIAN_AUTO_MIGRATE=false
ENV

cat > "$T/restart.sh" <<RESTART
#!/usr/bin/env bash
[[ -f "$T/pid" ]] && kill "\$(cat "$T/pid")" 2>/dev/null && sleep 1
cd "$T/app"
set -a; . "$T/env"; set +a
nohup "$T/app/.venv/bin/sebastian" serve >> "$T/server.log" 2>&1 &
echo \$! > "$T/pid"
RESTART
chmod +x "$T/restart.sh"

export APP_DIR="$T/app" ENV_FILE="$T/env" BACKUP_DIR="$T/data/backups" \
  HEALTH_URL="http://127.0.0.1:$PORT/health" RESTART_CMD="$T/restart.sh" HEALTH_TIMEOUT=20
api() { curl -fsS -H "Authorization: Bearer $KEY" -H 'content-type: application/json' "$@"; }
BASE="http://127.0.0.1:$PORT/api/v1"

echo "1) first deploy of v0.1.0"
"$REPO/deploy/deploy.sh" v0.1.0 >"$T/d1.log" 2>&1 || { cat "$T/d1.log"; fail "first deploy"; }
curl -fsS "$HEALTH_URL" | grep -q '"version":"0.1.0"' && pass "healthy, version 0.1.0"

api -X POST "$BASE/categories" -d '{"name":"Public Transport"}' >/dev/null
for _ in 1 2 3; do api -X POST "$BASE/entries" -d '{"category":"Public Transport"}' >/dev/null; done
api -X POST "$BASE/tasks" -d '{"title":"survives deploys"}' >/dev/null
pass "seeded 3 entries + 1 task"

echo "2) upgrade to v0.2.0 (adds a migration)"
"$REPO/deploy/deploy.sh" v0.2.0 >"$T/d2.log" 2>&1 || { cat "$T/d2.log"; fail "upgrade deploy"; }
curl -fsS "$HEALTH_URL" | grep -q '"schema":"0002"' && pass "schema migrated to 0002"
ls "$T/data/backups"/sebastian-*.db >/dev/null 2>&1 && pass "pre-deploy backup created"
[[ $(api "$BASE/entries" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') == 3 ]] && pass "entries survived"
api "$BASE/tasks" | grep -q "survives deploys" && pass "task survived"

echo "3) broken release v0.3.0 must roll back"
if "$REPO/deploy/deploy.sh" v0.3.0 >"$T/d3.log" 2>&1; then fail "broken deploy unexpectedly succeeded"; fi
grep -q "rolling back" "$T/d3.log" && pass "deploy detected failure and rolled back"
curl -fsS "$HEALTH_URL" | grep -q '"version":"0.2.0"' && pass "service healthy on v0.2.0 again"
[[ $(git -C "$T/app" describe --tags --exact-match) == v0.2.0 ]] && pass "checkout restored to v0.2.0"
[[ $(api "$BASE/entries" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') == 3 ]] && pass "data intact after rollback"

echo "4) re-deploy of the current tag is a safe no-op"
"$REPO/deploy/deploy.sh" v0.2.0 >"$T/d4.log" 2>&1 && pass "idempotent"

echo; echo "ALL DEPLOY CHECKS PASSED"
OK=1
