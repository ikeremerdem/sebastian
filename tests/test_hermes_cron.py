"""The Hermes cron helper, run against a real Sebastian server (no LLM involved)."""

import importlib.util
import json
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "hermes" / "sebastian_cron.py"


@pytest.fixture
def cron(live_server, tmp_path, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_URL", live_server)
    monkeypatch.setenv("SEBASTIAN_API_KEY", "test-key")
    monkeypatch.setenv("SEBASTIAN_CRON_STATE", str(tmp_path / "state.json"))
    spec = importlib.util.spec_from_file_location("sebastian_cron", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def api(method, path, body=None):
        req = urllib.request.Request(
            live_server + "/api/v1" + path,
            method=method,
            data=json.dumps(body or {}).encode(),
            headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read() or b"{}")

    mod.api = api
    return mod


def run(cron, capsys, mode):
    code = cron.main([mode])
    assert code == 0
    return capsys.readouterr().out


def due_in(minutes):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def test_nag_is_silent_when_nothing_is_due(cron, capsys):
    cron.api("POST", "/tasks", {"title": "later", "due_at": due_in(600)})
    assert run(cron, capsys, "nag") == ""


def test_nag_prints_once_then_waits_for_the_interval(cron, capsys):
    cron.api(
        "POST",
        "/series",
        {"title": "Collect rent", "rrule": "FREQ=DAILY", "note": "Flat 3B, 950 EUR",
         "due_time": "00:00", "nag_interval_min": 120, "quiet_hours": "none"},
    )  # fmt: skip
    task = cron.api("POST", "/tasks", {"title": "Call dentist", "due_at": due_in(-5),
                                       "note": "ask price", "quiet_hours": "none"})  # fmt: skip
    out = run(cron, capsys, "nag")
    assert "Call dentist" in out and "reminder #1" in out and f"id {task['id']}" in out
    assert "Last remark" in out and "ask price" in out
    assert "Collect rent" in out and "Flat 3B, 950 EUR" in out and "2 things need you" in out
    # reported as notified, so the very next tick has nothing to say
    assert run(cron, capsys, "nag") == ""
    assert cron.api("GET", f"/tasks/{task['id']}")["nag_count"] == 1


def test_nag_respects_snooze_and_done(cron, capsys):
    t = cron.api(
        "POST", "/tasks", {"title": "Pay card", "due_at": due_in(-5), "quiet_hours": "none"}
    )
    cron.api("POST", f"/tasks/{t['id']}/snooze", {"minutes": 60})
    assert run(cron, capsys, "nag") == ""
    cron.api("POST", f"/tasks/{t['id']}/unsnooze")
    assert "Pay card" in run(cron, capsys, "nag")
    cron.api("POST", f"/tasks/{t['id']}/done")
    assert run(cron, capsys, "nag") == ""


def test_brief_lists_sections_and_snoozed(cron, capsys):
    cron.api("POST", "/tasks", {"title": "Old thing", "due_at": due_in(-60 * 24 * 3)})
    cron.api("POST", "/tasks", {"title": "Today thing", "due_at": datetime.now(UTC).isoformat()})
    s = cron.api("POST", "/tasks", {"title": "Parked thing", "due_at": due_in(-30)})
    cron.api("POST", f"/tasks/{s['id']}/snooze", {"minutes": 300, "note": "later"})
    cron.api("POST", "/tasks", {"title": "Next week", "due_at": due_in(60 * 24 * 3)})
    out = run(cron, capsys, "brief")
    assert out.startswith("☀️ Good morning")
    assert "Overdue (1)" in out and "Old thing — 3 days overdue" in out
    assert "Today thing" in out
    assert "Snoozed (will resume) (1)" in out and "Parked thing — until" in out
    assert "Coming up (1)" in out and "Next week" in out


def test_brief_when_nothing_is_planned(cron, capsys):
    assert "Nothing planned" in run(cron, capsys, "brief")


def test_weekly_summary(cron, capsys):
    cron.api("POST", "/categories", {"name": "Public Transport"})
    for _ in range(3):
        cron.api("POST", "/entries", {"category": "Public Transport"})
    cron.api("POST", "/entries", {"category": "Note", "text": "Pizza Joint 18 EUR"})
    out = run(cron, capsys, "weekly")
    assert out.startswith("📊 Your week")
    assert "• Public Transport: 3 entries on 1 day" in out
    assert "Notes (1)" in out and "Pizza Joint 18 EUR" in out
    assert "Note:" not in out  # notes are listed, not counted as a category


def test_weekly_with_no_logs(cron, capsys):
    assert "Nothing logged this week" in run(cron, capsys, "weekly")


def test_outage_is_quiet_at_first_then_warns_once_and_announces_recovery(
    cron, capsys, monkeypatch, live_server
):
    monkeypatch.setenv("SEBASTIAN_URL", "http://127.0.0.1:9")  # nothing listens there
    assert run(cron, capsys, "nag") == ""  # 1st failure: quiet
    assert run(cron, capsys, "nag") == ""  # 2nd: quiet
    warned = run(cron, capsys, "nag")  # 3rd: one warning
    assert "Sebastian isn't answering" in warned
    assert run(cron, capsys, "nag") == ""  # and not again every tick
    monkeypatch.setenv("SEBASTIAN_URL", live_server)
    assert "Sebastian is back" in run(cron, capsys, "nag")
    assert run(cron, capsys, "nag") == ""


def test_daily_jobs_warn_on_the_first_failure(cron, capsys, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_URL", "http://127.0.0.1:9")
    assert "brief is paused" in run(cron, capsys, "brief")


def test_wrong_key_is_reported_not_crashed(cron, capsys, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_API_KEY", "nope")
    for _ in range(3):
        out = run(cron, capsys, "nag")
    assert "HTTP 401" in out


def test_bad_usage_exits_nonzero(cron, capsys):
    assert cron.main(["wat"]) == 2
    assert cron.main([]) == 2
