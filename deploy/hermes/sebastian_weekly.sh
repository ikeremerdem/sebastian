#!/usr/bin/env bash
# Hermes cron script (see sebastian_cron.py). Keep this file next to sebastian_cron.py.
exec python3 "$(dirname "$0")/sebastian_cron.py" weekly
