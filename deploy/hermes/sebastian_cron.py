#!/usr/bin/env python3
"""Hermes cron helper for Sebastian. No LLM, standard library only.

Run as a Hermes `--no-agent` cron script: whatever this prints is delivered to Telegram
verbatim; printing nothing stays silent.

    sebastian_cron.py nag      poll /due, print reminders, report them as notified
    sebastian_cron.py brief    morning brief from /digest/today
    sebastian_cron.py weekly   weekly log summary from /digest/entries

Config (environment, falling back to ~/.hermes/.env for the key):
    SEBASTIAN_URL      default http://127.0.0.1:8000
    SEBASTIAN_API_KEY  the bearer key
"""

import contextlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

WARN_AFTER = {"nag": 3, "brief": 1, "weekly": 1}  # consecutive failures before warning
REPEAT_WARNING_EVERY = 24  # ...then again every N further failures (nag: every 2h)


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------- API


def base_url() -> str:
    return os.environ.get("SEBASTIAN_URL", "http://127.0.0.1:8000").rstrip("/")


def api_key() -> str:
    key = os.environ.get("SEBASTIAN_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"\s*SEBASTIAN_API_KEY\s*=\s*(.*)$", line)
            if m:
                return m.group(1).strip().strip("'\"")
    raise ApiError("SEBASTIAN_API_KEY not found (set it in ~/.hermes/.env)")


def call(method: str, path: str) -> dict:
    req = urllib.request.Request(
        base_url() + path,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise ApiError(f"{method} {path} -> HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(f"cannot reach Sebastian at {base_url()} ({exc})") from exc


# ------------------------------------------------------------------------ modes


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def nag() -> str:
    items = call("GET", "/api/v1/due")["items"]
    blocks = []
    for item in items:
        try:
            # Mark first: if the message can't be delivered we miss one nag at worst,
            # but we can never spam every tick.
            call("POST", f"/api/v1/tasks/{item['id']}/notified")
        except ApiError:
            continue
        due = item["due_human"]
        if item["overdue"]:
            due += f" ({item['due_relative']})"
        lines = [f"🔔 {item['title']}", f"{due} · reminder #{item['nag_number']} · id {item['id']}"]
        if item.get("series_note"):
            lines.append(item["series_note"])
        if item.get("remarks"):
            lines.append("Last remark: " + item["remarks"].splitlines()[-1])
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    head = "" if len(blocks) == 1 else f"🔔 {len(blocks)} things need you\n\n"
    tail = '\n\nReply e.g. "<task> done", "snooze <task> 2h" or "skip <task>".'
    return head + "\n\n".join(blocks) + tail


def brief() -> str:
    d = call("GET", "/api/v1/digest/today")
    day = date.fromisoformat(d["date"])
    out = [f"☀️ Good morning · {day:%A} {day.day} {day:%B}"]
    sections = [
        ("Overdue", "overdue", lambda t: f"• {t['title']} — {t['due_relative']}"),
        ("Due today", "today", lambda t: f"• {t['due_human'].split(' · ')[-1]} {t['title']}"),
        (
            "Snoozed (will resume)",
            "snoozed",
            lambda t: f"• {t['title']} — until {t['snooze_human']}",
        ),
        ("Coming up", "upcoming", lambda t: f"• {t['due_human']} {t['title']}"),
    ]
    empty = True
    for label, key, fmt in sections:
        if d[key]:
            empty = False
            out.append(f"\n{label} ({len(d[key])})")
            out.extend(fmt(t) for t in d[key])
    if empty:
        out.append("\nNothing planned. Enjoy the day.")
    return "\n".join(out)


def weekly() -> str:
    d = call("GET", "/api/v1/digest/entries?period=week")
    out = [f"📊 Your week · {d['from']} → {d['to']}"]
    counted = [c for c in d["categories"] if c["category"] != "Note"]
    if counted:
        out.append("")
        for c in counted:
            out.append(
                f"• {c['category']}: {plural(c['total_entries'], 'entry', 'entries')} "
                f"on {plural(c['distinct_days'], 'day')}"
            )
    else:
        out.append("\nNothing logged this week.")
    notes = d.get("notes", [])
    if notes:
        out.append(f"\n📝 Notes ({len(notes)})")
        out.extend(f"• {n['created_local'][:10]}: {n['text']}" for n in notes[:10])
    return "\n".join(out)


MODES = {"nag": nag, "brief": brief, "weekly": weekly}


# ------------------------------------------------------- failure tracking / main


def state_path() -> Path:
    default = Path.home() / ".hermes" / "scripts" / ".sebastian_cron_state.json"
    return Path(os.environ.get("SEBASTIAN_CRON_STATE", default))


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with contextlib.suppress(OSError):  # state is a nicety; never fail the job over it
        state_path().write_text(json.dumps(state))


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in MODES:
        print(f"usage: sebastian_cron.py {'|'.join(MODES)}", file=sys.stderr)
        return 2
    mode = argv[0]
    state = load_state()
    failures = state.get(mode, 0)
    try:
        text = MODES[mode]()
    except ApiError as exc:
        # Stay quiet for blips, but tell the user once Sebastian has been down for a while.
        failures += 1
        state[mode] = failures
        save_state(state)
        threshold = WARN_AFTER[mode]
        if failures == threshold or (
            failures > threshold and (failures - threshold) % REPEAT_WARNING_EVERY == 0
        ):
            print(f"⚠️ Sebastian isn't answering, so {mode} is paused: {exc}")
        return 0
    if failures:
        state[mode] = 0
        save_state(state)
        if failures >= WARN_AFTER[mode]:
            print("✅ Sebastian is back.")
            if text:
                print()
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
