"""Single source of 'now' so tests can freeze time (freezegun patches datetime)."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
