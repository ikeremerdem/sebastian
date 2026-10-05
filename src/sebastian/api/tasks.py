from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import schemas as sc
from ..clock import utcnow
from ..services import settings as settings_svc
from ..services import tasks as svc
from .deps import get_session

router = APIRouter()


def _tasks(session, rows):
    eff, now = settings_svc.get_effective(session), utcnow()
    return [sc.task_out(t, eff, now) for t in rows]


def _task(session, t):
    return _tasks(session, [t])[0]


# ------------------------------------------------------------------ tasks


@router.post("/tasks", status_code=201, tags=["tasks"])
def create_task(body: sc.TaskCreate, session: Session = Depends(get_session)):
    return _task(session, svc.create_task(session, **body.model_dump()))


@router.get("/tasks", tags=["tasks"])
def list_tasks(
    status: str = Query("all", description="waiting|open|snoozed|done|skipped|active|all"),
    series_id: int | None = None,
    q: str | None = Query(None, description="title search"),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    rows = svc.list_tasks(
        session,
        status=status,
        series_id=series_id,
        text=q,
        start=_aware(start),
        end=_aware(end),
        limit=limit,
    )
    return _tasks(session, rows)


@router.get("/tasks/{task_id}", tags=["tasks"])
def get_task(task_id: int, session: Session = Depends(get_session)):
    return _task(session, svc.get_task(session, task_id))


@router.patch("/tasks/{task_id}", tags=["tasks"])
def update_task(task_id: int, body: sc.TaskUpdate, session: Session = Depends(get_session)):
    return _task(session, svc.update_task(session, task_id, **body.model_dump(exclude_unset=True)))


@router.delete("/tasks/{task_id}", status_code=204, tags=["tasks"])
def delete_task(task_id: int, session: Session = Depends(get_session)):
    svc.delete_task(session, task_id)


@router.post("/tasks/{task_id}/done", tags=["tasks"])
def done(task_id: int, body: sc.NoteBody = sc.NoteBody(), session: Session = Depends(get_session)):
    return _task(session, svc.complete_task(session, task_id, body.note))


@router.post("/tasks/{task_id}/skip", tags=["tasks"])
def skip(task_id: int, body: sc.NoteBody = sc.NoteBody(), session: Session = Depends(get_session)):
    return _task(session, svc.skip_task(session, task_id, body.note))


@router.post("/tasks/{task_id}/snooze", tags=["tasks"])
def snooze(task_id: int, body: sc.SnoozeBody, session: Session = Depends(get_session)):
    return _task(
        session,
        svc.snooze_task(
            session, task_id, until=_aware(body.until), minutes=body.minutes, note=body.note
        ),
    )


@router.post("/tasks/{task_id}/unsnooze", tags=["tasks"])
def unsnooze(
    task_id: int, body: sc.NoteBody = sc.NoteBody(), session: Session = Depends(get_session)
):
    return _task(session, svc.unsnooze_task(session, task_id, body.note))


@router.post("/tasks/{task_id}/remark", tags=["tasks"])
def remark(task_id: int, body: sc.NoteBody, session: Session = Depends(get_session)):
    return _task(session, svc.add_remark(session, task_id, body.note or ""))


@router.post("/tasks/{task_id}/reopen", tags=["tasks"])
def reopen(
    task_id: int, body: sc.NoteBody = sc.NoteBody(), session: Session = Depends(get_session)
):
    return _task(session, svc.reopen_task(session, task_id, body.note))


@router.post("/tasks/{task_id}/notified", tags=["nagging"])
def notified(task_id: int, session: Session = Depends(get_session)):
    """Hermes reports it delivered a nag for this task."""
    return _task(session, svc.mark_notified(session, task_id))


# -------------------------------------------------------- polling + digest


@router.get("/due", tags=["nagging"])
def due(session: Session = Depends(get_session)):
    """What to nag about right now. Polling is read-only; call /notified after delivering."""
    now = utcnow()
    eff = settings_svc.get_effective(session)
    items = []
    for task, reason in svc.get_due(session, now):
        item = sc.task_out(task, eff, now)
        item["reason"] = reason  # due | nag | overdue
        item["nag_number"] = task.nag_count + 1
        items.append(item)
    return {"now": now, "timezone": eff.timezone, "count": len(items), "items": items}


@router.get("/digest/today", tags=["digest"])
def digest_today(
    upcoming_days: int = Query(7, ge=0, le=60), session: Session = Depends(get_session)
):
    now = utcnow()
    eff = settings_svc.get_effective(session)
    d = svc.digest_today(session, now, upcoming_days)
    out = {"date": d["date"], "timezone": d["timezone"]}
    for key in ("today", "overdue", "snoozed", "upcoming"):
        out[key] = [sc.task_out(t, eff, now) for t in d[key]]
    return out


# ------------------------------------------------------------------ series


@router.post("/series", status_code=201, tags=["series"])
def create_series(body: sc.SeriesCreate, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)
    return sc.series_out(svc.create_series(session, **body.model_dump()), eff)


@router.get("/series", tags=["series"])
def list_series(include_inactive: bool = False, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)
    return [sc.series_out(s, eff) for s in svc.list_series(session, include_inactive)]


@router.get("/series/{series_id}", tags=["series"])
def get_series(series_id: int, session: Session = Depends(get_session)):
    return sc.series_out(svc.get_series(session, series_id), settings_svc.get_effective(session))


@router.patch("/series/{series_id}", tags=["series"])
def update_series(series_id: int, body: sc.SeriesUpdate, session: Session = Depends(get_session)):
    series = svc.update_series(session, series_id, **body.model_dump(exclude_unset=True))
    return sc.series_out(series, settings_svc.get_effective(session))


@router.delete("/series/{series_id}", status_code=204, tags=["series"])
def archive_series(series_id: int, session: Session = Depends(get_session)):
    """Archives the series (history is kept; future untouched occurrences are removed)."""
    svc.delete_series(session, series_id)


# ---------------------------------------------------------------- settings


@router.get("/settings", tags=["settings"])
def get_settings(session: Session = Depends(get_session)):
    return settings_svc.get_effective(session).as_dict()


@router.patch("/settings", tags=["settings"])
def patch_settings(body: sc.SettingsUpdate, session: Session = Depends(get_session)):
    return settings_svc.update_settings(session, **body.model_dump()).as_dict()


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt
