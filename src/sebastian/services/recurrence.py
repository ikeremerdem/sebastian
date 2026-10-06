"""RRULE expansion in the series' local timezone (DST-safe) -> UTC instants."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rrulestr

from .errors import Invalid
from .timeutil import get_tz, parse_hhmm

MAX_PER_PASS = 400


def build_rule(rrule_str: str, dtstart: date, due_time: str) -> rrule:
    text = (rrule_str or "").strip()
    if not text:
        raise Invalid("rrule is required for a series")
    if "DTSTART" in text.upper():
        raise Invalid("do not include DTSTART in rrule; use the dtstart field")
    t = parse_hhmm(due_time)
    start = datetime.combine(dtstart, t)  # naive local wall-clock
    try:
        rule = rrulestr(text, dtstart=start)
    except (ValueError, TypeError) as exc:
        raise Invalid(f"invalid rrule {rrule_str!r}: {exc}") from exc
    if not isinstance(rule, rrule):
        raise Invalid("rrule must describe a single recurrence rule")
    return rule


def to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    return local_naive.replace(tzinfo=tz).astimezone(UTC)


def to_local_naive(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz).replace(tzinfo=None)


def occurrences_to_generate(
    rule: rrule,
    tz: ZoneInfo,
    now: datetime,
    generated_through: datetime | None,
) -> list[datetime]:
    """UTC due instants not yet generated, up to and including the first one after now.

    On first generation, occurrences earlier than the start of today (local) are
    skipped so creating a series with an old dtstart doesn't flood with a backlog.
    """
    now_local = to_local_naive(now, tz)
    if generated_through is None:
        after = datetime.combine(now_local.date(), datetime.min.time()) - timedelta(microseconds=1)
    else:
        after = to_local_naive(generated_through, tz)
    nxt = rule.after(now_local, inc=False)
    upper = nxt if nxt is not None else now_local
    out: list[datetime] = []
    for occ in rule.between(after, upper, inc=True):
        if occ <= after:
            continue
        out.append(to_utc(occ, tz))
        if len(out) >= MAX_PER_PASS:
            break
    return out


def validate_series_fields(rrule_str: str, dtstart: date, due_time: str, tz_name: str) -> None:
    get_tz(tz_name)
    build_rule(rrule_str, dtstart, due_time)


# ------------------------------------------------------- friendly rule helpers

_DAY_NAMES = {
    "MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun",
}  # fmt: skip
_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAY_CODES = list(_DAY_NAMES)


def build_rrule_string(
    kind: str,
    *,
    interval: int = 1,
    day: str | int | None = None,
    month: int | None = None,
    weekdays: list[str] | None = None,
) -> str:
    """Friendly picker -> RRULE. kind: daily | weekly | monthly | yearly."""
    if interval < 1:
        raise Invalid("repeat interval must be at least 1")
    iv = f";INTERVAL={interval}" if interval > 1 else ""
    if kind == "daily":
        return f"FREQ=DAILY{iv}"
    if kind == "weekly":
        days = [d for d in (weekdays or []) if d in _DAY_NAMES]
        if not days:
            raise Invalid("pick at least one weekday")
        return f"FREQ=WEEKLY{iv};BYDAY={','.join(days)}"
    if kind in ("monthly", "yearly"):
        if str(day).lower() == "last":
            day_n = -1
        else:
            try:
                day_n = int(day)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise Invalid("day of month must be a number from 1 to 31, or 'last'") from exc
            if not 1 <= day_n <= 31:
                raise Invalid("day of month must be between 1 and 31")
        if kind == "monthly":
            return f"FREQ=MONTHLY{iv};BYMONTHDAY={day_n}"
        if not month or not 1 <= month <= 12:
            raise Invalid("pick a month")
        return f"FREQ=YEARLY{iv};BYMONTH={month};BYMONTHDAY={day_n}"
    raise Invalid(f"unknown repeat kind {kind!r}")


def describe(rrule_str: str, due_time: str = "") -> str:
    """'FREQ=MONTHLY;BYMONTHDAY=5' -> 'Every month on day 5 · 09:00' (raw rule if unusual)."""
    at = f" · {due_time}" if due_time else ""
    try:
        parts = dict(p.split("=", 1) for p in rrule_str.strip().split(";") if "=" in p)
        if set(parts) - {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH"}:
            raise ValueError
        freq, n = parts["FREQ"], int(parts.get("INTERVAL", 1))
        unit = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}[freq]
        every = f"Every {unit}" if n == 1 else f"Every {n} {unit}s"
        if freq == "WEEKLY" and "BYDAY" in parts:
            every += " on " + ", ".join(_DAY_NAMES[d] for d in parts["BYDAY"].split(","))
        elif freq == "MONTHLY" and "BYMONTHDAY" in parts:
            d = int(parts["BYMONTHDAY"])
            every += " on the last day" if d == -1 else f" on day {d}"
        elif freq == "YEARLY" and "BYMONTHDAY" in parts and "BYMONTH" in parts:
            d = int(parts["BYMONTHDAY"])
            m = _MONTH_NAMES[int(parts["BYMONTH"]) - 1]
            every += f" on {'the last day of' if d == -1 else d} {m}"
        elif set(parts) - {"FREQ", "INTERVAL"}:
            raise ValueError
        return every + at
    except (KeyError, ValueError, IndexError):
        return rrule_str + at
