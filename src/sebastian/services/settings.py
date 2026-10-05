"""Runtime-editable settings (DB overrides on top of env defaults)."""

from dataclasses import asdict, dataclass
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import get_config
from ..models import Setting
from .errors import Invalid
from .timeutil import get_tz, parse_quiet_hours

KEYS = ("timezone", "default_nag_interval_min", "quiet_hours")


@dataclass
class Effective:
    timezone: str
    default_nag_interval_min: int
    quiet_hours: str

    @property
    def tz(self) -> ZoneInfo:
        return get_tz(self.timezone)

    def as_dict(self) -> dict:
        return asdict(self)


def get_effective(session: Session) -> Effective:
    cfg = get_config()
    values = {
        "timezone": cfg.timezone,
        "default_nag_interval_min": str(cfg.default_nag_interval_min),
        "quiet_hours": cfg.quiet_hours,
    }
    for row in session.query(Setting).all():
        if row.key in values:
            values[row.key] = row.value
    return Effective(
        timezone=values["timezone"],
        default_nag_interval_min=int(values["default_nag_interval_min"]),
        quiet_hours=values["quiet_hours"],
    )


def update_settings(session: Session, **changes) -> Effective:
    for key, value in changes.items():
        if value is None:
            continue
        if key not in KEYS:
            raise Invalid(f"unknown setting {key!r}")
        if key == "timezone":
            get_tz(value)
        elif key == "quiet_hours":
            parse_quiet_hours(value)
        elif key == "default_nag_interval_min" and int(value) < 1:
            raise Invalid("default_nag_interval_min must be >= 1")
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=str(value)))
        else:
            row.value = str(value)
    session.commit()
    return get_effective(session)
