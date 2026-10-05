"""Timezone, quiet-hours and local-date helpers."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import Invalid

_QUIET_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")


def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Invalid(f"unknown timezone {name!r} (use an IANA name like Europe/Berlin)") from exc


def parse_hhmm(value: str) -> time:
    m = _HHMM_RE.match(value or "")
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise Invalid(f"invalid time {value!r}; expected HH:MM (24h)")
    return time(int(m[1]), int(m[2]))


def parse_quiet_hours(spec: str | None) -> tuple[time, time] | None:
    """'22:00-07:00' -> (22:00, 07:00); 'none'/''/None -> None (no quiet hours)."""
    if spec is None or spec.strip().lower() in ("", "none"):
        return None
    m = _QUIET_RE.match(spec.strip())
    if not m:
        raise Invalid(f"invalid quiet_hours {spec!r}; expected HH:MM-HH:MM or 'none'")
    start, end = parse_hhmm(f"{m[1]}:{m[2]}"), parse_hhmm(f"{m[3]}:{m[4]}")
    return start, end


def in_quiet_hours(now: datetime, spec: str | None, tz: ZoneInfo) -> bool:
    window = parse_quiet_hours(spec)
    if window is None:
        return False
    start, end = window
    if start == end:
        return False
    t = now.astimezone(tz).time().replace(second=0, microsecond=0)
    if start < end:
        return start <= t < end
    return t >= start or t < end  # wraps midnight


def local_date(dt: datetime, tz: ZoneInfo) -> date:
    return dt.astimezone(tz).date()


def local_midnight_utc(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(UTC)


def fmt_local(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else None


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # ISO week starts Monday
