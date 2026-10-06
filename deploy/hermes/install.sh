#!/usr/bin/env bash
# Copy Sebastian's Hermes cron scripts into ~/.hermes/scripts (Hermes only runs scripts from there).
# Re-run after a Sebastian update to refresh them.
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
dest=${HERMES_SCRIPTS:-$HOME/.hermes/scripts}
mkdir -p "$dest"
for f in sebastian_cron.py sebastian_nag.sh sebastian_brief.sh sebastian_weekly.sh; do
  install -m 755 "$src/$f" "$dest/$f"
done
echo "Installed into $dest:"; ls -1 "$dest" | grep '^sebastian_' | sed 's/^/  /'
