"""Quick-add: turn "call dentist tomorrow 9:30" into a title and a due time.

Deliberately small and predictable: a handful of unambiguous patterns, evaluated in the
user's timezone. Anything it doesn't recognise stays in the title, so a wrong guess is
never silent: the caller shows the interpreted due time and offers Undo.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIME = time(9, 0)
WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}  # fmt: skip
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip
DAYPARTS = {"morning": time(9, 0), "noon": time(12, 0), "afternoon": time(15, 0),
            "evening": time(19, 0), "tonight": time(20, 0)}  # fmt: skip

_UNITS = {"m": "minutes", "min": "minutes", "mins": "minutes", "minute": "minutes",
          "minutes": "minutes", "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours",
          "hours": "hours", "d": "days", "day": "days", "days": "days", "w": "weeks",
          "week": "weeks", "weeks": "weeks"}  # fmt: skip

_RELATIVE = re.compile(
    r"\bin\s+(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"(minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b",
    re.I,
)
_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}  # fmt: skip
_TIME_COLON = re.compile(r"\b(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.I)
_TIME_AMPM = re.compile(r"\b(?:at\s+)?(\d{1,2})\s*(am|pm)\b", re.I)
_TIME_AT = re.compile(r"\bat\s+(\d{1,2})\b(?!\s*[:/.\-])", re.I)
_DAYPART = re.compile(r"\b(?:in the\s+|this\s+)?(" + "|".join(DAYPARTS) + r")\b", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d:])")
_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(
    rf"\b(?:on\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\b(?:\s+(\d{{4}}))?",
    re.I,
)
_MONTH_DAY = re.compile(
    rf"\b(?:on\s+)?({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I
)
_RELDAY = re.compile(r"\b(today|tomorrow|tmrw|tmr)\b", re.I)
_WEEKDAY = re.compile(
    r"\b(?:(?:on|next|this|coming)\s+)?("
    + "|".join(sorted(WEEKDAYS, key=len, reverse=True))
    + r")\b",
    re.I,
)
_EDGE_WORDS = re.compile(
    r"^(?:(?:on|at|by|for|due|until|before)\b[\s,.:;-]*)+|[\s,.:;-]+(?:on|at|by|for|due|until|before)$",
    re.I,
)


@dataclass
class Parsed:
    title: str
    due: datetime | None  # timezone-aware UTC, or None = no date given (due now)
    matched: bool  # True if a date/time was recognised


def _cut(text: str, m: re.Match) -> str:
    return (text[: m.start()] + " " + text[m.end() :]).strip()


def _hour(h: int, mer: str | None) -> int | None:
    if mer:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if mer.lower() == "pm" else 0)
    return h if 0 <= h <= 23 else None


def _safe_date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse(text: str, now: datetime, tz: ZoneInfo) -> Parsed:
    original = " ".join(text.split())
    rest = original
    local_now = now.astimezone(tz)
    today = local_now.date()
    when_date: date | None = None
    when_time: time | None = None
    delta: timedelta | None = None

    # 1. "in 2 hours", "in 3 days"
    if m := _RELATIVE.search(rest):
        n = _WORDNUM.get(m[1].lower()) or int(m[1])
        unit = _UNITS[m[2].lower()]
        delta = timedelta(**{unit: n})
        rest = _cut(rest, m)

    # 2. explicit clock times (colon, am/pm, or "at 9"); the first one wins
    if delta is None or delta >= timedelta(days=1):
        for pattern in (_TIME_COLON, _TIME_AMPM, _TIME_AT):
            if m := pattern.search(rest):
                groups = m.groups()
                h = int(groups[0])
                minute = int(groups[1]) if pattern is _TIME_COLON else 0
                mer = groups[-1] if pattern is not _TIME_AT else None
                hour = _hour(h, mer)
                if hour is not None and 0 <= minute <= 59:
                    when_time = time(hour, minute)
                    rest = _cut(rest, m)
                    break

    # 3. parts of the day: "tomorrow morning", "tonight", "noon"
    if m := _DAYPART.search(rest):
        word = m[1].lower()
        if when_time is None:
            when_time = DAYPARTS[word]
        if word == "tonight" and when_date is None and delta is None:
            when_date = today
        rest = _cut(rest, m)

    # 4. calendar dates
    if delta is None:
        if m := _ISO.search(rest):
            when_date = _safe_date(int(m[1]), int(m[2]), int(m[3]))
            if when_date:
                rest = _cut(rest, m)
        if when_date is None and (m := _DAY_MONTH.search(rest)):
            when_date = _resolve_year(today, MONTHS[m[2].lower()], int(m[1]), m[3])
            if when_date:
                rest = _cut(rest, m)
        if when_date is None and (m := _MONTH_DAY.search(rest)):
            when_date = _resolve_year(today, MONTHS[m[1].lower()], int(m[2]), m[3])
            if when_date:
                rest = _cut(rest, m)
        if when_date is None and (m := _DMY.search(rest)):
            year = m[3]
            if year and len(year) == 2:
                year = "20" + year
            when_date = _resolve_year(today, int(m[2]), int(m[1]), year)  # European d/m
            if when_date:
                rest = _cut(rest, m)
        if when_date is None and (m := _RELDAY.search(rest)):
            word = m[1].lower()
            when_date = today + timedelta(days=0 if word == "today" else 1)
            rest = _cut(rest, m)
        if when_date is None and (m := _WEEKDAY.search(rest)):
            target = WEEKDAYS[m[1].lower()]
            ahead = (target - today.weekday()) % 7 or 7  # always the next one, never today
            when_date = today + timedelta(days=ahead)
            rest = _cut(rest, m)

    # 5. combine
    due_local: datetime | None = None
    if delta is not None:
        base = local_now + delta
        due_local = (
            datetime.combine(base.date(), when_time, tzinfo=tz)
            if when_time and delta >= timedelta(days=1)
            else base.replace(second=0, microsecond=0)
        )
    elif when_date is not None:
        due_local = datetime.combine(when_date, when_time or DEFAULT_TIME, tzinfo=tz)
    elif when_time is not None:
        candidate = datetime.combine(today, when_time, tzinfo=tz)
        due_local = candidate if candidate > local_now else candidate + timedelta(days=1)

    title = _EDGE_WORDS.sub("", rest.strip(" ,.;:-")).strip(" ,.;:-")
    if due_local is None or not title:
        # nothing recognised (or nothing left to call it): keep the text exactly as typed
        return Parsed(title=original, due=None, matched=False)
    return Parsed(title=title[0].upper() + title[1:], due=due_local.astimezone(UTC), matched=True)


def _resolve_year(today: date, month: int, day: int, year: str | None) -> date | None:
    if year:
        return _safe_date(int(year), month, day)
    d = _safe_date(today.year, month, day)
    if d is not None and d < today:
        d = _safe_date(today.year + 1, month, day)  # "5 jan" in December means next January
    return d
