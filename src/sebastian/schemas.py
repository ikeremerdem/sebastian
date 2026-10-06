"""Pydantic request/response models and ORM -> output serializers."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .clock import utcnow
from .models import Category, Entry, Series, Task
from .services import settings as settings_svc
from .services import tasks as task_svc
from .services.recurrence import describe as describe_rrule
from .services.timeutil import fmt_local, local_date

# ------------------------------------------------------------------- requests


class TaskCreate(BaseModel):
    title: str
    due_at: datetime | None = Field(None, description="ISO 8601 with offset; default: now")
    note: str = ""
    nag_interval_min: int | None = None
    quiet_hours: str | None = Field(None, description="'HH:MM-HH:MM' or 'none'")


class TaskUpdate(BaseModel):
    title: str | None = None
    due_at: datetime | None = None
    nag_interval_min: int | None = None
    quiet_hours: str | None = None


class NoteBody(BaseModel):
    note: str | None = None


class SnoozeBody(BaseModel):
    until: datetime | None = None
    minutes: int | None = None
    note: str | None = None


class SeriesCreate(BaseModel):
    title: str
    rrule: str = Field(
        description="iCalendar RRULE without DTSTART, e.g. FREQ=MONTHLY;BYMONTHDAY=5"
    )
    note: str = ""
    dtstart: date | None = None
    due_time: str = "09:00"
    nag_interval_min: int | None = None
    quiet_hours: str | None = None
    tz: str | None = None


class SeriesUpdate(BaseModel):
    title: str | None = None
    rrule: str | None = None
    note: str | None = None
    dtstart: date | None = None
    due_time: str | None = None
    nag_interval_min: int | None = None
    quiet_hours: str | None = None
    tz: str | None = None
    active: bool | None = None


class SettingsUpdate(BaseModel):
    timezone: str | None = None
    default_nag_interval_min: int | None = None
    quiet_hours: str | None = None


class CategoryCreate(BaseModel):
    name: str
    description: str = ""
    requires_text: bool = False


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    requires_text: bool | None = None
    archived: bool | None = None


class MergeBody(BaseModel):
    into: str | int


class EntryCreate(BaseModel):
    category: str | int
    text: str | None = None
    at: datetime | None = Field(None, description="Backdate; default: now")


class EntryUpdate(BaseModel):
    category: str | int | None = None
    text: str | None = None
    at: datetime | None = None


# ------------------------------------------------------------------ responses


def human_when(dt: datetime | None, tz: ZoneInfo, now: datetime) -> str | None:
    """'Today · 09:00', 'Tomorrow · 09:00', 'Mon 5 Oct · 09:00' (adds the year if not this year)."""
    if dt is None:
        return None
    local, today = dt.astimezone(tz), now.astimezone(tz).date()
    delta = (local.date() - today).days
    if delta == 0:
        day = "Today"
    elif delta == 1:
        day = "Tomorrow"
    elif delta == -1:
        day = "Yesterday"
    else:
        day = f"{local:%a} {local.day} {local:%b}" + (
            f" {local.year}" if local.year != today.year else ""
        )
    return f"{day} · {local:%H:%M}"


def relative_days(dt: datetime, tz: ZoneInfo, now: datetime, overdue_if_past: bool) -> str:
    delta = (dt.astimezone(tz).date() - now.astimezone(tz).date()).days
    if delta == 0:
        return "due today"
    if delta > 0:
        return "tomorrow" if delta == 1 else f"in {delta} days"
    n = -delta
    word = "overdue" if overdue_if_past else "ago"
    return f"1 day {word}" if n == 1 else f"{n} days {word}"


def task_out(task: Task, eff: settings_svc.Effective, now: datetime | None = None) -> dict:
    now = now or utcnow()
    tz = eff.tz
    state = task.status
    if task_svc.is_snoozed(task, now):
        state = "snoozed"
    series = task.series
    return {
        "id": task.id,
        "title": task.title,
        "series_id": task.series_id,
        "series_note": series.note if series else None,
        "recurring": task.series_id is not None,
        "occurrence": local_date(task.due_at, tz).isoformat() if task.series_id else None,
        "status": task.status,
        "state": state,
        "due_at": task.due_at,
        "due_local": fmt_local(task.due_at, tz),
        "due_human": human_when(task.due_at, tz, now),
        "due_relative": relative_days(task.due_at, tz, now, task.status == "open"),
        "done_human": human_when(task.done_at, tz, now),
        "snooze_human": human_when(task.snooze_until, tz, now) if state == "snoozed" else None,
        "overdue": task.status == "open" and local_date(task.due_at, tz) < local_date(now, tz),
        "done_at": task.done_at,
        "done_local": fmt_local(task.done_at, tz),
        "snooze_until": task.snooze_until if state == "snoozed" else None,
        "snooze_until_local": fmt_local(task.snooze_until, tz) if state == "snoozed" else None,
        "nag_interval_min": task_svc.effective_nag_interval(task, eff),
        "quiet_hours": task_svc.effective_quiet_hours(task, eff),
        "nag_count": task.nag_count,
        "last_notified_at": task.last_notified_at,
        "remarks": task.remarks,
    }


def series_out(series: Series, eff: settings_svc.Effective) -> dict:
    return {
        "id": series.id,
        "title": series.title,
        "note": series.note,
        "rrule": series.rrule,
        "schedule_text": describe_rrule(series.rrule, series.due_time),
        "dtstart": series.dtstart,
        "due_time": series.due_time,
        "tz": series.tz,
        "nag_interval_min": series.nag_interval_min,
        "effective_nag_interval_min": series.nag_interval_min or eff.default_nag_interval_min,
        "quiet_hours": series.quiet_hours,
        "active": series.active,
    }


def category_out(cat: Category, count: int | None = None) -> dict:
    out = {
        "id": cat.id,
        "name": cat.name,
        "description": cat.description,
        "requires_text": cat.requires_text,
        "archived": cat.archived,
    }
    if count is not None:
        out["entry_count"] = count
    return out


def entry_out(entry: Entry, eff: settings_svc.Effective) -> dict:
    return {
        "id": entry.id,
        "category": entry.category.name,
        "text": entry.text,
        "created_at": entry.created_at,
        "created_local": fmt_local(entry.created_at, eff.tz),
    }
