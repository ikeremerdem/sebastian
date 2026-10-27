from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from sebastian.services.quickadd import parse

TZ = ZoneInfo("Europe/Berlin")
# Tuesday 27 Oct 2026, 09:00 local (CET, UTC+1)
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)


def local(text):
    p = parse(text, NOW, TZ)
    return p.title, (p.due.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if p.due else None)


@pytest.mark.parametrize(
    "text, title, due",
    [
        ("call dentist tomorrow 9:30", "Call dentist", "2026-10-28 09:30"),
        ("tomorrow call dentist", "Call dentist", "2026-10-28 09:00"),
        ("pay rent friday", "Pay rent", "2026-10-30 09:00"),
        ("pay rent on friday", "Pay rent", "2026-10-30 09:00"),
        ("by friday send invoice", "Send invoice", "2026-10-30 09:00"),
        ("submit report next monday 14:00", "Submit report", "2026-11-02 14:00"),
        ("gym tuesday", "Gym", "2026-11-03 09:00"),  # today is Tuesday: means next week
        ("call mom at 5pm", "Call mom", "2026-10-27 17:00"),
        ("call mom 5pm", "Call mom", "2026-10-27 17:00"),
        ("water plants at 8am", "Water plants", "2026-10-28 08:00"),  # already past today
        ("stand-up at 14:30", "Stand-up", "2026-10-27 14:30"),
        ("lunch at 12", "Lunch", "2026-10-27 12:00"),
        ("check oven in 2 hours", "Check oven", "2026-10-27 11:00"),
        ("in 90 minutes stretch", "Stretch", "2026-10-27 10:30"),
        ("in an hour take pill", "Take pill", "2026-10-27 10:00"),
        ("follow up in 3 days", "Follow up", "2026-10-30 09:00"),
        ("report in 3 days at 14:00", "Report", "2026-10-30 14:00"),
        ("renew passport 5 nov", "Renew passport", "2026-11-05 09:00"),
        ("renew passport on nov 5th", "Renew passport", "2026-11-05 09:00"),
        ("renew passport 5 november 2027", "Renew passport", "2027-11-05 09:00"),
        ("dentist 5.11.", "Dentist", "2026-11-05 09:00"),
        ("dentist 5/11 10:00", "Dentist", "2026-11-05 10:00"),
        ("buy gift 2026-12-24 18:00", "Buy gift", "2026-12-24 18:00"),
        ("party 5 jan", "Party", "2027-01-05 09:00"),  # already passed this year
        ("meeting 25 oct", "Meeting", "2027-10-25 09:00"),
        ("pack bag tonight", "Pack bag", "2026-10-27 20:00"),
        ("gym tomorrow morning", "Gym", "2026-10-28 09:00"),
        ("call bank tomorrow afternoon", "Call bank", "2026-10-28 15:00"),
        ("sunday 10:00 brunch", "Brunch", "2026-11-01 10:00"),
    ],
)
def test_understood_phrases(text, title, due):
    assert local(text) == (title, due)


@pytest.mark.parametrize(
    "text",
    [
        "buy milk",
        "read version 3.14 release notes",  # 3.14 is not a date
        "call 555-1234",
        "tomorrow",  # nothing left to call the task
        "friday",
        "at 25",
    ],
)
def test_unrecognised_text_is_kept_verbatim_with_no_due_time(text):
    p = parse(text, NOW, TZ)
    assert p.title == text and p.due is None and p.matched is False


def test_result_is_timezone_aware_utc():
    p = parse("call dentist tomorrow 9:30", NOW, TZ)
    assert p.due.tzinfo is not None and p.due.utcoffset().total_seconds() == 0
    assert p.due == datetime(2026, 10, 28, 8, 30, tzinfo=UTC)  # CET: 09:30 local = 08:30 UTC


def test_dst_change_is_respected():
    # 28 March 2027: clocks jump forward; "monday 09:00" afterwards is CEST (UTC+2)
    now = datetime(2027, 3, 24, 8, 0, tzinfo=UTC)
    p = parse("report monday 09:00", now, TZ)
    assert p.due == datetime(2027, 3, 29, 7, 0, tzinfo=UTC)


def test_impossible_dates_are_not_guessed():
    assert parse("party 31 feb", NOW, TZ).due is None
    assert parse("party 2026-13-45", NOW, TZ).due is None
