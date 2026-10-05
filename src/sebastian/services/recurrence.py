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
