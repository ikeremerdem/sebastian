# Sebastian

*At your service.* Sebastian is a small, self-hosted backend for **tasks, reminders, logs and notes**, built to be
driven by an AI agent ([Hermes](#7-connecting-hermes)) over Telegram, with a web UI for when you want to look at things yourself.
The name nods to the obedient servant of Turkish pop culture: it does what it is told, remembers everything, and
never lets you forget the rent.

- **Recurring obligations that nag until you say "done"** (rent, bills, ...). Snoozing only pauses; the next
  occurrence still arrives on schedule, and unpaid ones never silently expire.
- **Logs for later analysis** ("record public transport" → count per month, distinct days per year).
- **Notes** ("take note that the pizza cost 18 EUR"), timestamped automatically.
- **Passive store:** Sebastian never calls out and holds no Telegram token. Hermes *polls* it, delivers the
  messages itself, and reports back. If Hermes is down, nothing is lost; reminders stay due.

```
Telegram <-> Hermes (polls /due, schedules the morning brief + weekly summary)
                |  MCP at /mcp  or  REST at /api/v1   (Authorization: Bearer <key>)
                v
          Sebastian (FastAPI) --- SQLite (WAL) --- backups
                ^
   your Mac: web UI (password login, over an SSH tunnel)
```

## Contents
1. [Concepts](#1-concepts)
2. [Quick start (Mac)](#2-quick-start-mac)
3. [Configuration](#3-configuration)
4. [API and MCP](#4-api-and-mcp)
5. [Deploying to a Raspberry Pi 5](#5-deploying-to-a-raspberry-pi-5)
6. [Backups and data safety](#6-backups-and-data-safety)
7. [Connecting Hermes](#7-connecting-hermes)
8. [Using the web UI from your Mac](#8-using-the-web-ui-from-your-mac)
9. [Development](#9-development)
10. [Roadmap](#10-roadmap)

---

## 1. Concepts

**Task**: anything actionable, with a due time. Statuses: `waiting` (due in the future), `open` (due, being nagged),
`done`, `skipped`. *Snoozed* is an `open` task whose `snooze_until` is in the future.
Every task has `remarks`: an append-only, timestamped history (`2026-04-05 10:02 snoozed until ...: waiting for bank`).

**Series**: a template for something that repeats ("collect rent, every month on the 5th at 09:00"). It generates one
task per occurrence, **by the calendar, not by completion**. If April's rent is still open when May's arrives, both are
open and both nag. Use *skip* if an occurrence genuinely doesn't apply. Recurrence uses iCalendar
[RRULE](https://icalendar.org/iCalendar-RFC-5545/3-8-5-3-recurrence-rule.html) (without `DTSTART`):

| Meaning | `rrule` |
|---|---|
| 5th of every month | `FREQ=MONTHLY;BYMONTHDAY=5` |
| Last day of every month | `FREQ=MONTHLY;BYMONTHDAY=-1` |
| Mondays and Thursdays | `FREQ=WEEKLY;BYDAY=MO,TH` |
| Every 2 weeks | `FREQ=WEEKLY;INTERVAL=2` |
| 15 June every year | `FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=15` |

(`BYMONTHDAY=31` skips months without a 31st; use `-1` for "last day".) Times are wall-clock in the series' timezone
and DST-safe. A one-off task is just a task without a series.

**Nagging rules**: a task is returned by `GET /due` while it is `open`, not snoozed, outside quiet hours, and at least
`nag_interval_min` minutes have passed since it was last reported as notified. **Only `done` or `skip` stops nagging.**
Snooze has no cap, so the morning brief always lists snoozed items with their wake-up time.
Defaults (interval, quiet hours) are global settings and can be overridden per series/task.

**Category and entry**: notes and counted occurrences share one table. Each *entry* belongs to a *category* that has an
agent-facing `description` and a `requires_text` flag. A seeded **Note** category (text required) covers plain notes;
`Public Transport` would be a category with no text needed. Categories are enforced: logging to an unknown category is
rejected with the list of valid ones, so counts never drift ("Public Transport" vs "transit").

**Summaries** count `entries` and `distinct_days` (calendar days in your timezone) per category and week/month/year,
which answers "did I use public transport on fewer than 250 days this year?".

| You say to Hermes | What it calls |
|---|---|
| "Remind me to call the dentist tomorrow at 9" | `create_task` |
| "Every month on the 5th check whether the tenant paid" | `create_series` |
| "Rent is paid" | `complete_task` (with your remark) |
| "Snooze that for 2 hours, tenant said tonight" | `snooze_task` |
| "Record public transport" | `add_entry(category="Public Transport")` |
| "Take note that the pizza cost 18 EUR" | `add_entry(category="Note", text=...)` |
| "How often did I take public transport each month?" | `entry_summary` |
| "What's waiting / due / done?" | `list_tasks` |

## 2. Quick start (Mac)

Prerequisites: [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 itself) and git.

```bash
git clone <your-repo-url> sebastian && cd sebastian
uv sync
cp .env.example .env
```

Edit `.env`: generate secrets and a UI password hash, and set your timezone:

```bash
uv run sebastian genkey          # run twice: API key and session secret
uv run sebastian hash-password   # prompts for the UI password; paste the output as SEBASTIAN_UI_PASSWORD_HASH
```

Run it (migrations are applied automatically on startup):

```bash
uv run sebastian serve           # http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000> for the UI, <http://127.0.0.1:8000/docs> for the interactive API docs.
Try the API:

```bash
export KEY=...   # your SEBASTIAN_API_KEY
curl -H "Authorization: Bearer $KEY" localhost:8000/api/v1/due
```

Run the tests: `uv run pytest`. Lint/format: `uv run ruff check . && uv run ruff format .`.

## 3. Configuration

All settings are environment variables (prefix `SEBASTIAN_`), read from the process environment or a `.env` file.
Values containing `$` (the password hash) should be wrapped in single quotes.

| Variable | Default | Meaning |
|---|---|---|
| `SEBASTIAN_DATABASE_PATH` | `data/sebastian.db` | SQLite file. In production keep it **outside the checkout**. |
| `SEBASTIAN_API_KEY` | (required) | Bearer token for `/api/v1` and `/mcp`. `sebastian genkey`. |
| `SEBASTIAN_SESSION_SECRET` | falls back to the API key | Signs the UI session cookie. |
| `SEBASTIAN_UI_PASSWORD_HASH` | (empty = UI login disabled) | Argon2 hash from `sebastian hash-password`. |
| `SEBASTIAN_TIMEZONE` | `UTC` | IANA timezone for due times, quiet hours and "days". **Set this.** |
| `SEBASTIAN_DEFAULT_NAG_INTERVAL_MIN` | `60` | Default minutes between nags. |
| `SEBASTIAN_QUIET_HOURS` | `22:00-07:00` | Local window with no nags; `none` disables. |
| `SEBASTIAN_AUTO_MIGRATE` | `true` | Run Alembic migrations when the server starts. |
| `SEBASTIAN_HOST` / `SEBASTIAN_PORT` | `127.0.0.1` / `8000` | Bind address. Keep it on localhost. |

Timezone, default nag interval and quiet hours can also be changed later in the UI (**Settings**) or via
`PATCH /api/v1/settings`; those values override the env defaults.

CLI: `sebastian serve | migrate | genkey | hash-password | backup --to DIR [--keep N]`.

## 4. API and MCP

**Authentication.** Every `/api/v1/*` and `/mcp` request needs `Authorization: Bearer <SEBASTIAN_API_KEY>`.
`GET /health` is open (used by deploys).

**REST** (`/api/v1`, full reference at `/docs`):

| Area | Endpoints |
|---|---|
| Nagging | `GET /due`, `POST /tasks/{id}/notified` |
| Tasks | `POST/GET /tasks` (`?status=waiting\|open\|snoozed\|done\|skipped\|active\|all&q=&from=&to=`), `GET/PATCH/DELETE /tasks/{id}` (any task can be deleted), `POST /tasks/{id}/done\|skip\|snooze\|unsnooze\|remark\|reopen` |
| Series | `POST/GET /series`, `GET/PATCH/DELETE /series/{id}` (DELETE archives; history kept) |
| Categories | `GET/POST /categories`, `PATCH /categories/{id}`, `POST /categories/{id}/merge` |
| Entries | `POST/GET /entries` (`?category=&q=&from=&to=`), `GET/PATCH/DELETE /entries/{id}`, `GET /entries/summary?category=&from=&to=&group_by=week\|month\|year\|none` |
| Digests | `GET /digest/today`, `GET /digest/entries?period=week\|last_week\|last_7_days\|month\|last_month\|year` |
| Settings | `GET/PATCH /settings` |

Errors are JSON `{"detail": ...}`. Logging to an unknown category returns `422` with `valid_categories`.
Times are ISO 8601 and **must carry a UTC offset**; summaries use local calendar dates.

**MCP** (streamable HTTP at `/mcp`, same bearer key). Tools:
`get_due`, `mark_notified`, `get_digest_today`, `list_tasks`, `create_task`, `complete_task`, `snooze_task`,
`skip_task`, `add_task_remark`, `reopen_task`, `create_series`, `list_series`, `archive_series`, `list_categories`,
`create_category`, `add_entry`, `search_entries`, `entry_summary`, `get_digest_entries`, `get_settings`.
The server ships instructions that explain the nag loop and category rules to the agent.

## 5. Deploying to a Raspberry Pi 5

The model: **git tags + `uv` + systemd**. No Docker. Code comes from tagged releases of your private GitHub repo;
data lives outside the checkout, so a deploy can never overwrite it.

```
/opt/sebastian               git checkout (code only, owned by user "sebastian")
/var/lib/sebastian/          sebastian.db and backups/ (never in git)
/etc/sebastian/env           secrets and settings (root:sebastian, mode 640)
```

### One-time setup

Do this on the Pi, as your normal admin user.

1. **Service user, and a read-only deploy key for the repo:**

   ```bash
   sudo useradd --system --create-home --home-dir /var/lib/sebastian --shell /bin/bash sebastian
   sudo install -d -o sebastian -g sebastian /opt/sebastian
   sudo -u sebastian ssh-keygen -t ed25519 -N "" -f /var/lib/sebastian/.ssh/id_ed25519
   sudo cat /var/lib/sebastian/.ssh/id_ed25519.pub
   ```

   Add the printed key to GitHub: *repo → Settings → Deploy keys* (leave "write access" **off**).

2. **Clone, and install `uv` for the service user:**

   ```bash
   sudo -u sebastian git clone git@github.com:<you>/sebastian.git /opt/sebastian
   sudo -u sebastian sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
   ```

3. **Install the service, backup timer and sudo rule** (idempotent; creates `/etc/sebastian/env` with fresh secrets):

   ```bash
   sudo /opt/sebastian/deploy/install.sh
   sudoedit /etc/sebastian/env          # set SEBASTIAN_TIMEZONE, review the rest
   ```

4. **First deploy**, then set the UI password:

   ```bash
   sudo -u sebastian /opt/sebastian/deploy/deploy.sh latest
   sudo -u sebastian /opt/sebastian/.venv/bin/sebastian hash-password
   # paste the hash into /etc/sebastian/env as SEBASTIAN_UI_PASSWORD_HASH='$argon2id$...' (keep the quotes)
   sudo systemctl restart sebastian
   ```

5. **Check it:**

   ```bash
   curl -s localhost:8000/health        # {"status":"ok","version":"0.1.0","schema":"0001"}
   journalctl -u sebastian -f           # logs
   systemctl list-timers sebastian-backup.timer
   ```

   The API key for Hermes is `SEBASTIAN_API_KEY` in `/etc/sebastian/env`.

### Releasing and deploying an update

On your Mac:

```bash
# 1. bump `version` in pyproject.toml, commit, merge to main
git tag v0.2.0 && git push origin main v0.2.0
# 2. deploy
ssh pi sudo -u sebastian /opt/sebastian/deploy/deploy.sh v0.2.0     # or: ... deploy.sh latest
```

`deploy.sh <tag|latest>` does, in order: **back up the database** → check out the tag → `uv sync --frozen` →
**run migrations** → restart the service → poll `/health`. If any step fails or the service doesn't become healthy,
it **rolls back** to the previous version automatically and prints the backup path. Re-running it for the current tag is
a safe no-op.

**Migrations must be additive** (new tables/columns, backwards-compatible), so the previous release still works
against a migrated database if a rollback is needed. Destructive changes (dropping/renaming columns) need their own
release with a reviewed migration and a mention in the release notes.

You can rehearse the whole thing locally, without touching real data: `./scripts/verify-deploy.sh` builds a scratch origin
with three tagged releases (including one with a broken migration), deploys them in turn, and asserts that data survives
and the rollback works.

## 6. Backups and data safety

- The database is a single SQLite file (`/var/lib/sebastian/sebastian.db`, WAL mode).
- **Before every deploy**, `deploy.sh` takes a consistent online backup (safe while running) into
  `/var/lib/sebastian/backups/` and keeps the latest 14.
- A **nightly timer** (`sebastian-backup.timer`, 03:30) does the same and keeps 30.
- Manual backup: `sudo -u sebastian /opt/sebastian/.venv/bin/sebastian backup --to /var/lib/sebastian/backups`.
- Copy backups off the Pi occasionally, e.g. from your Mac:
  `rsync -a pi:/var/lib/sebastian/backups/ ~/Backups/sebastian/` (an SD card is not a backup).

**Restore** a backup:

```bash
sudo systemctl stop sebastian
sudo -u sebastian cp /var/lib/sebastian/backups/sebastian-<timestamp>.db /var/lib/sebastian/sebastian.db
sudo rm -f /var/lib/sebastian/sebastian.db-wal /var/lib/sebastian/sebastian.db-shm
sudo systemctl start sebastian      # migrations bring an older backup up to date
```

## 7. Connecting Hermes

Hermes runs on the same Pi, so it talks to `127.0.0.1:8000`. There are two separate jobs: **chat** (you talk to Hermes
on Telegram and it calls Sebastian's tools) and **scheduled messages** (nags, morning brief, weekly summary).

### Chat: register Sebastian as an MCP server

Keep the key out of `config.yaml` by putting it in Hermes' `.env` and referring to it by name.

```bash
# ~/.hermes/.env   (copy the value from SEBASTIAN_API_KEY in /etc/sebastian/env)
SEBASTIAN_API_KEY=...
```

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  sebastian:
    url: "http://127.0.0.1:8000/mcp"
    headers:
      Authorization: "Bearer ${SEBASTIAN_API_KEY}"
    timeout: 30
    connect_timeout: 10
```

Check it with `hermes mcp test sebastian` (it should list 20 tools). Hermes prefixes tool names with the server name
(`mcp_sebastian_get_due`, ...). The gateway reloads its config within about a minute; `sudo systemctl restart hermes-gateway` forces it.
The tool descriptions and the server's instructions tell the agent how nags, categories and snoozing work, so
"rent is paid", "snooze it for 2 hours" or "record public transport" just work. Tell Hermes once to *use Sebastian for
tasks, reminders, logs and notes* if it picks other tools.

### Scheduled messages: Hermes cron, without an LLM

Nags, the morning brief and the weekly summary are plain Hermes `--no-agent` cron jobs: a small script runs, its output
is delivered to Telegram verbatim, and **empty output stays silent**. No model is involved, so they cost no tokens, are
fast, and keep working if your LLM provider is down. The scripts live in `deploy/hermes/`; Hermes only runs scripts from
`~/.hermes/scripts/`, so install (and after each Sebastian update, refresh) them with:

```bash
/opt/sebastian/deploy/hermes/install.sh
```

Then create the three jobs (delivery goes to your Telegram home channel):

```bash
hermes cron create "every 5m"    --no-agent --script sebastian_nag.sh    --name "Sebastian: nags"           --deliver telegram
hermes cron create "30 7 * * *"  --no-agent --script sebastian_brief.sh  --name "Sebastian: morning brief"  --deliver telegram
hermes cron create "0 18 * * 0"  --no-agent --script sebastian_weekly.sh --name "Sebastian: weekly summary" --deliver telegram
```

- **Nags** (`sebastian_nag.sh`): asks `GET /due` every 5 minutes. Nothing due, nothing sent. Otherwise one message listing each
  due task (occurrence date, your remarks, how often you've been nagged) and each is reported as notified so the next nag
  waits for its interval. Sebastian itself enforces snooze, quiet hours and the per-task interval. If Sebastian is
  unreachable for ~15 minutes you get one warning, and a note when it's back.
- **Morning brief** (07:30): overdue, due today, snoozed (with wake-up times) and coming up.
- **Weekly summary** (Sunday 18:00): entries and distinct days per category for the week, plus the notes you wrote.

Reply to a nag in Telegram ("rent done", "snooze rent 2h") and Hermes handles it through the MCP tools. Test a job at any
time with `hermes cron run <job-id>`, list them with `hermes cron list`. The helper reads its key from `SEBASTIAN_API_KEY`
or `~/.hermes/.env` and its address from `SEBASTIAN_URL` (default `http://127.0.0.1:8000`).

**REST instead of MCP:** use the `/api/v1` endpoints above with the same bearer header; `/openapi.json` describes them.

## 8. Using the web UI from your Mac

Sebastian binds to `127.0.0.1` on the Pi and is never exposed publicly. Reach the UI through an SSH tunnel:

```bash
ssh -N -L 8000:127.0.0.1:8000 pi        # then open http://localhost:8000
```

(Add `LocalForward 8000 127.0.0.1:8000` to the `pi` entry in `~/.ssh/config` to make it a one-word command.)
[Tailscale](https://tailscale.com/) works too: `sudo tailscale serve --bg 8000` on the Pi gives an HTTPS URL on your
tailnet without exposing a port to the internet.

The UI is a responsive app (light and dark, phone-friendly):

- **Today**: stat tiles (overdue / due today / snoozed / coming up), grouped task rows with a one-click done circle,
  one-tap logging for your categories and a quick-note box.
- **Tasks**: filter by status, search, and per-task actions in a "⋯" menu: *Done with note*, *Snooze*, *Skip*,
  *Add remark*, *Reopen* and **Delete**. Notes are entered in dialogs; everything is also available from the task's detail page.
- **Recurring**: a repeat picker (monthly day or "last day", weekdays, daily, yearly, or a custom RRULE) with
  readable schedules like "Every month on day 5 · 09:00".
- **Logs & notes**: add, filter, search and summarise (entries and distinct days per week/month/year).
- **Categories**: descriptions for the agent, merge and archive. **Settings**: timezone, nag interval, quiet hours.

Deleting a task is permanent. For a recurring occurrence it removes that occurrence only; the series carries on and
the deleted one is not regenerated. Prefer *Skip* when you want a record that it didn't happen.
Login is rate-limited and forms are CSRF-protected.

## 9. Development

```
src/sebastian/
  config.py db.py models.py schemas.py auth.py app.py main.py   settings, ORM, FastAPI app factory
  services/        all business logic (tasks, recurrence, nagging, entries, settings); REST/MCP/UI are thin adapters
  api/             REST routers          mcp_server.py   MCP tools          ui/   server-rendered web UI
  migrations/      Alembic migrations (applied on startup and by `sebastian migrate`)
deploy/            systemd units, install.sh, deploy.sh; hermes/ = cron scripts for Hermes      scripts/verify-deploy.sh   deployment rehearsal
tests/             pytest (frozen-clock tests for recurrence, nagging and summaries; MCP tested with the real client)
```

- **Add a migration:** change `models.py`, then `uv run alembic -c alembic.ini revision --autogenerate -m "describe it" --rev-id 0002`
  (it uses `SEBASTIAN_DATABASE_PATH`; point it at a throwaway DB at head). Review the generated file, keep it additive,
  and add `import sebastian.db` if it references `UTCDateTime`.
- **Time in tests:** use `freezegun.freeze_time`; the app reads time through `sebastian.clock.utcnow`.
- **Release:** all tests green → `./scripts/verify-deploy.sh` → bump `version` in `pyproject.toml` → merge → tag `vX.Y.Z` → push
  tag → `deploy.sh vX.Y.Z` on the Pi.

## 10. Roadmap

Deliberately **not** in v1: events/calendar (and external calendar sync), habits, projects and subtasks, and exports
(notes → Markdown "memories", Apple Notes). Data is stored raw and portable (stable ids, UTC timestamps) so exports can be
added without redesign.
