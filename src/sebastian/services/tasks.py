"""Tasks and series: lifecycle, lazy materialisation, nagging and digests."""

import threading
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from ..clock import utcnow
from ..models import Series, Task
from . import recurrence
from . import settings as settings_svc
from .errors import Conflict, Invalid, NotFound
from .timeutil import (
    fmt_local,
    get_tz,
    in_quiet_hours,
    local_date,
    local_midnight_utc,
    parse_quiet_hours,
)

_materialize_lock = threading.Lock()

FINAL = ("done", "skipped")


# ---------------------------------------------------------------- lifecycle sync


def sync(session: Session, now: datetime | None = None) -> None:
    """Generate due occurrences for active series and promote waiting -> open."""
    now = now or utcnow()
    with _materialize_lock:
        for series in session.scalars(select(Series).where(Series.active.is_(True))):
            _materialize_series(session, series, now)
        session.execute(
            update(Task).where(Task.status == "waiting", Task.due_at <= now).values(status="open")
        )
        session.commit()


def _materialize_series(session: Session, series: Series, now: datetime) -> None:
    tz = get_tz(series.tz)
    rule = recurrence.build_rule(series.rrule, series.dtstart, series.due_time)
    due_list = recurrence.occurrences_to_generate(rule, tz, now, series.generated_through)
    for due_at in due_list:
        session.add(
            Task(
                title=series.title,
                series_id=series.id,
                due_at=due_at,
                status="open" if due_at <= now else "waiting",
                nag_interval_min=series.nag_interval_min,
                quiet_hours=series.quiet_hours,
            )
        )
    if due_list:
        series.generated_through = due_list[-1]


# ---------------------------------------------------------------------- series


def create_series(
    session: Session,
    *,
    title: str,
    rrule: str,
    note: str = "",
    dtstart: date | None = None,
    due_time: str = "09:00",
    nag_interval_min: int | None = None,
    quiet_hours: str | None = None,
    tz: str | None = None,
) -> Series:
    eff = settings_svc.get_effective(session)
    tz_name = tz or eff.timezone
    now = utcnow()
    start = dtstart or local_date(now, eff.tz if tz is None else get_tz(tz_name))
    title = _clean_title(title)
    recurrence.validate_series_fields(rrule, start, due_time, tz_name)
    _validate_nag(nag_interval_min, quiet_hours)
    series = Series(
        title=title,
        note=note or "",
        rrule=rrule.strip(),
        dtstart=start,
        tz=tz_name,
        due_time=due_time,
        nag_interval_min=nag_interval_min,
        quiet_hours=quiet_hours,
    )
    session.add(series)
    session.commit()
    sync(session, now)
    return series


def get_series(session: Session, series_id: int) -> Series:
    series = session.get(Series, series_id)
    if series is None:
        raise NotFound(f"series {series_id} not found")
    return series


def list_series(session: Session, include_inactive: bool = False) -> list[Series]:
    q = select(Series).order_by(Series.title)
    if not include_inactive:
        q = q.where(Series.active.is_(True))
    return list(session.scalars(q))


def update_series(session: Session, series_id: int, **changes) -> Series:
    sync(session)  # promote anything already due so it isn't treated as untouched-future
    series = get_series(session, series_id)
    if "title" in changes and changes["title"] is not None:
        changes["title"] = _clean_title(changes["title"])
    new_rrule = changes.get("rrule") or series.rrule
    new_start = changes.get("dtstart") or series.dtstart
    new_time = changes.get("due_time") or series.due_time
    new_tz = changes.get("tz") or series.tz
    schedule_changed = any(
        changes.get(k) is not None and changes[k] != getattr(series, k)
        for k in ("rrule", "dtstart", "due_time", "tz")
    )
    recurrence.validate_series_fields(new_rrule, new_start, new_time, new_tz)
    if "nag_interval_min" in changes or "quiet_hours" in changes:
        _validate_nag(changes.get("nag_interval_min"), changes.get("quiet_hours"))
    for key, value in changes.items():
        if key in ("active", "nag_interval_min", "quiet_hours") or value is not None:
            setattr(series, key, value)
    if schedule_changed:
        # Future untouched occurrences are regenerated from the new schedule.
        for task in session.scalars(
            select(Task).where(Task.series_id == series.id, Task.status == "waiting")
        ):
            session.delete(task)
        last_open = session.scalar(
            select(Task.due_at)
            .where(Task.series_id == series.id)
            .order_by(Task.due_at.desc())
            .limit(1)
        )
        series.generated_through = last_open
    # Propagate presentation/nag fields to unfinished tasks of this series.
    for task in session.scalars(
        select(Task).where(Task.series_id == series.id, Task.status.not_in(FINAL))
    ):
        task.title = series.title
        task.nag_interval_min = series.nag_interval_min
        task.quiet_hours = series.quiet_hours
    session.commit()
    sync(session)
    return series


def delete_series(session: Session, series_id: int) -> None:
    """Archive a series (keeps history); open/waiting tasks are removed if untouched."""
    sync(session)
    series = get_series(session, series_id)
    series.active = False
    for task in session.scalars(
        select(Task).where(Task.series_id == series.id, Task.status == "waiting")
    ):
        session.delete(task)
    session.commit()


# ----------------------------------------------------------------------- tasks


def create_task(
    session: Session,
    *,
    title: str,
    due_at: datetime | None = None,
    note: str = "",
    nag_interval_min: int | None = None,
    quiet_hours: str | None = None,
) -> Task:
    now = utcnow()
    due = due_at or now
    if due.tzinfo is None:
        raise Invalid("due_at must include a timezone offset, e.g. 2026-04-05T09:00:00+02:00")
    _validate_nag(nag_interval_min, quiet_hours)
    task = Task(
        title=_clean_title(title),
        due_at=due,
        status="open" if due <= now else "waiting",
        nag_interval_min=nag_interval_min,
        quiet_hours=quiet_hours,
    )
    session.add(task)
    session.flush()
    if note:
        _remark(session, task, "note", note, now)
    session.commit()
    return task


def get_task(session: Session, task_id: int) -> Task:
    sync(session)
    task = session.scalars(
        select(Task).options(joinedload(Task.series)).where(Task.id == task_id)
    ).first()
    if task is None:
        raise NotFound(f"task {task_id} not found")
    return task


def list_tasks(
    session: Session,
    *,
    status: str = "all",
    series_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    text: str | None = None,
    limit: int = 200,
) -> list[Task]:
    """status: waiting | open | snoozed | done | skipped | active (waiting+open) | all."""
    sync(session)
    now = utcnow()
    q = select(Task).options(joinedload(Task.series))
    if status == "snoozed":
        q = q.where(Task.status == "open", Task.snooze_until > now)
    elif status == "active":
        q = q.where(Task.status.in_(("waiting", "open")))
    elif status != "all":
        if status not in ("waiting", "open", "done", "skipped"):
            raise Invalid(f"unknown status {status!r}")
        q = q.where(Task.status == status)
    if series_id is not None:
        q = q.where(Task.series_id == series_id)
    if text:
        q = q.where(Task.title.ilike(f"%{text.strip()}%"))
    if start is not None:
        q = q.where(Task.due_at >= start)
    if end is not None:
        q = q.where(Task.due_at < end)
    order = Task.due_at.desc() if status in ("done", "skipped", "all") else Task.due_at.asc()
    return list(session.scalars(q.order_by(order, Task.id).limit(limit)))


def update_task(session: Session, task_id: int, **changes) -> Task:
    task = get_task(session, task_id)
    if changes.get("title") is not None:
        task.title = _clean_title(changes["title"])
    if changes.get("due_at") is not None:
        if changes["due_at"].tzinfo is None:
            raise Invalid("due_at must include a timezone offset")
        task.due_at = changes["due_at"]
        if task.status in ("waiting", "open"):
            task.status = "open" if task.due_at <= utcnow() else "waiting"
            if task.status == "waiting":
                task.nag_count, task.last_notified_at = 0, None
    if "nag_interval_min" in changes or "quiet_hours" in changes:
        _validate_nag(changes.get("nag_interval_min"), changes.get("quiet_hours"))
        if "nag_interval_min" in changes:
            task.nag_interval_min = changes["nag_interval_min"]
        if "quiet_hours" in changes:
            task.quiet_hours = changes["quiet_hours"]
    session.commit()
    return task


def delete_task(session: Session, task_id: int) -> None:
    task = get_task(session, task_id)
    if task.series_id is not None:
        raise Conflict(
            "tasks generated by a series can't be deleted; use skip, or archive the series"
        )
    session.delete(task)
    session.commit()


def complete_task(session: Session, task_id: int, note: str | None = None) -> Task:
    task = get_task(session, task_id)
    if task.status in FINAL:
        raise Conflict(f"task {task_id} is already {task.status}")
    now = utcnow()
    task.status, task.done_at = "done", now
    task.snooze_until = None
    _remark(session, task, "done", note, now)
    session.commit()
    return task


def skip_task(session: Session, task_id: int, note: str | None = None) -> Task:
    task = get_task(session, task_id)
    if task.status in FINAL:
        raise Conflict(f"task {task_id} is already {task.status}")
    now = utcnow()
    task.status, task.done_at = "skipped", now
    task.snooze_until = None
    _remark(session, task, "skipped", note, now)
    session.commit()
    return task


def snooze_task(
    session: Session,
    task_id: int,
    *,
    until: datetime | None = None,
    minutes: int | None = None,
    note: str | None = None,
) -> Task:
    task = get_task(session, task_id)
    if task.status != "open":
        raise Conflict(f"only open tasks can be snoozed (task {task_id} is {task.status})")
    now = utcnow()
    if (until is None) == (minutes is None):
        raise Invalid("give exactly one of 'until' or 'minutes'")
    if until is not None:
        if until.tzinfo is None:
            raise Invalid("until must include a timezone offset")
        wake = until
    else:
        if minutes < 1:
            raise Invalid("minutes must be >= 1")
        wake = now + timedelta(minutes=minutes)
    if wake <= now:
        raise Invalid("snooze time must be in the future")
    task.snooze_until = wake
    tz = settings_svc.get_effective(session).tz
    _remark(session, task, f"snoozed until {fmt_local(wake, tz)}", note, now)
    session.commit()
    return task


def unsnooze_task(session: Session, task_id: int, note: str | None = None) -> Task:
    task = get_task(session, task_id)
    if task.snooze_until is None or task.snooze_until <= utcnow():
        raise Conflict(f"task {task_id} is not snoozed")
    task.snooze_until = None
    _remark(session, task, "unsnoozed", note, utcnow())
    session.commit()
    return task


def add_remark(session: Session, task_id: int, note: str) -> Task:
    if not (note or "").strip():
        raise Invalid("note must not be empty")
    task = get_task(session, task_id)
    _remark(session, task, "note", note, utcnow())
    session.commit()
    return task


def reopen_task(session: Session, task_id: int, note: str | None = None) -> Task:
    task = get_task(session, task_id)
    if task.status not in FINAL:
        raise Conflict(f"task {task_id} is not done/skipped")
    now = utcnow()
    task.status = "open" if task.due_at <= now else "waiting"
    task.done_at = task.snooze_until = task.last_notified_at = None
    task.nag_count = 0
    _remark(session, task, "reopened", note, now)
    session.commit()
    return task


def mark_notified(session: Session, task_id: int) -> Task:
    """Hermes reports it delivered a nag; starts the next interval."""
    task = get_task(session, task_id)
    if task.status != "open":
        raise Conflict(f"task {task_id} is {task.status}; only open tasks are nagged")
    task.nag_count += 1
    task.last_notified_at = utcnow()
    session.commit()
    return task


# ------------------------------------------------------------------ nag engine


def effective_nag_interval(task: Task, eff: settings_svc.Effective) -> int:
    return task.nag_interval_min or eff.default_nag_interval_min


def effective_quiet_hours(task: Task, eff: settings_svc.Effective) -> str:
    return task.quiet_hours if task.quiet_hours is not None else eff.quiet_hours


def is_snoozed(task: Task, now: datetime) -> bool:
    return task.status == "open" and task.snooze_until is not None and task.snooze_until > now


def nag_due(task: Task, now: datetime, eff: settings_svc.Effective) -> bool:
    if task.status != "open" or is_snoozed(task, now):
        return False
    if in_quiet_hours(now, effective_quiet_hours(task, eff), eff.tz):
        return False
    if task.last_notified_at is None:
        return True
    return now - task.last_notified_at >= timedelta(minutes=effective_nag_interval(task, eff))


def get_due(session: Session, now: datetime | None = None) -> list[tuple[Task, str]]:
    """Open tasks that should be nagged about right now, with a reason."""
    now = now or utcnow()
    sync(session, now)
    eff = settings_svc.get_effective(session)
    today = local_date(now, eff.tz)
    out = []
    q = (
        select(Task)
        .options(joinedload(Task.series))
        .where(Task.status == "open")
        .order_by(Task.due_at)
    )
    for task in session.scalars(q):
        if not nag_due(task, now, eff):
            continue
        if task.nag_count == 0:
            reason = "due"
        elif local_date(task.due_at, eff.tz) < today:
            reason = "overdue"
        else:
            reason = "nag"
        out.append((task, reason))
    return out


# ---------------------------------------------------------------------- digest


def digest_today(session: Session, now: datetime | None = None, upcoming_days: int = 7) -> dict:
    now = now or utcnow()
    sync(session, now)
    eff = settings_svc.get_effective(session)
    tz = eff.tz
    today = local_date(now, tz)
    horizon = local_midnight_utc(today + timedelta(days=upcoming_days + 1), tz)
    rows = session.scalars(
        select(Task)
        .options(joinedload(Task.series))
        .where(Task.status.in_(("waiting", "open")), Task.due_at < horizon)
        .order_by(Task.due_at)
    ).all()
    out: dict = {
        "date": today.isoformat(),
        "timezone": eff.timezone,
        "today": [],
        "overdue": [],
        "snoozed": [],
        "upcoming": [],
    }
    for task in rows:
        due_day = local_date(task.due_at, tz)
        if is_snoozed(task, now):
            out["snoozed"].append(task)
        elif task.status == "open" and due_day < today:
            out["overdue"].append(task)
        elif due_day == today:
            out["today"].append(task)
        elif task.status == "waiting" and due_day > today:
            out["upcoming"].append(task)
    return out


# --------------------------------------------------------------------- helpers


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        raise Invalid("title must not be empty")
    if len(title) > 200:
        raise Invalid("title too long (max 200 characters)")
    return title


def _validate_nag(nag_interval_min: int | None, quiet_hours: str | None) -> None:
    if nag_interval_min is not None and nag_interval_min < 1:
        raise Invalid("nag_interval_min must be >= 1")
    if quiet_hours is not None:
        parse_quiet_hours(quiet_hours)


def _remark(session: Session, task: Task, action: str, note: str | None, now: datetime) -> None:
    tz = settings_svc.get_effective(session).tz
    stamp = now.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    line = f"{stamp} {action}" + (f": {note.strip()}" if note and note.strip() else "")
    task.remarks = (task.remarks + "\n" if task.remarks else "") + line
