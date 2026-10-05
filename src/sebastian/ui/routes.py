"""Server-rendered web UI (Jinja2 + plain forms; no JS build, no CDN)."""

import secrets
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import schemas as sc
from ..api.deps import get_session
from ..clock import utcnow
from ..config import get_config
from ..services import entries as entry_svc
from ..services import settings as settings_svc
from ..services import tasks as task_svc
from ..services.errors import SebastianError
from ..services.timeutil import get_tz, local_date

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter(include_in_schema=False)
_hasher = PasswordHasher()
_failures: dict[str, list[float]] = defaultdict(list)

SNOOZE_CHOICES = [
    (30, "30 min"),
    (60, "1 hour"),
    (180, "3 hours"),
    (1440, "1 day"),
    (4320, "3 days"),
]


# --------------------------------------------------------------------- helpers


def _csrf(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append([kind, message])


def render(request: Request, name: str, **ctx):
    msgs = request.session.pop("flash", [])
    base = {
        "csrf": _csrf(request),
        "flashes": msgs,
        "nav": request.url.path,
        "snooze_choices": SNOOZE_CHOICES,
    }
    return templates.TemplateResponse(request, name, {**base, **ctx})


def safe_next(value: str | None, default: str) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else default


def back(request: Request, form, default: str) -> RedirectResponse:
    return RedirectResponse(safe_next(form.get("next"), default), status_code=303)


def parse_local_dt(value: str | None, tz) -> datetime | None:
    """<input type=datetime-local> value in the user's timezone -> aware UTC datetime."""
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SebastianError(f"invalid date/time {value!r}") from exc
    return naive.replace(tzinfo=tz).astimezone(UTC)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SebastianError(f"invalid date {value!r}") from exc


def opt_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise SebastianError(f"{value!r} is not a whole number") from exc


def opt_str(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def guard(request: Request):
    """Dependency: redirect anonymous visitors to the login page."""
    if not authed(request):
        raise _Redirect("/login")


class _Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


async def csrf_guard(request: Request):
    if request.method == "POST" and request.url.path != "/login":
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "x")):
            raise SebastianError("form expired; please retry")


async def run_action(request: Request, default_next: str, fn):
    """Common POST handling: run fn(form) and flash the result or the domain error."""
    form = await request.form()
    try:
        message = fn(form)
        if message:
            flash(request, message)
    except SebastianError as exc:
        text = exc.message
        if exc.extra.get("valid_categories"):
            names = ", ".join(c["name"] for c in exc.extra["valid_categories"])
            text += f" (valid: {names})"
        flash(request, text, "error")
    return back(request, form, default_next)


# ----------------------------------------------------------------------- login


@router.get("/login")
async def login_page(request: Request):
    if authed(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", configured=bool(get_config().ui_password_hash))


@router.post("/login")
async def login(request: Request):
    cfg = get_config()
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "x")):
        flash(request, "Form expired; please try again.", "error")
        return RedirectResponse("/login", status_code=303)
    ip = request.client.host if request.client else "?"
    now = time.time()
    _failures[ip] = [t for t in _failures[ip] if now - t < 900]
    if len(_failures[ip]) >= 5:
        flash(request, "Too many attempts; try again in 15 minutes.", "error")
        return RedirectResponse("/login", status_code=303)
    try:
        if not cfg.ui_password_hash:
            raise VerifyMismatchError()
        _hasher.verify(cfg.ui_password_hash, str(form.get("password", "")))
    except Exception:
        _failures[ip].append(now)
        flash(request, "Wrong password.", "error")
        return RedirectResponse("/login", status_code=303)
    request.session.clear()
    request.session["auth"] = True
    _csrf(request)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ----------------------------------------------------------------------- pages

page = APIRouter(dependencies=[Depends(guard), Depends(csrf_guard)], include_in_schema=False)


def _eff_now(session):
    return settings_svc.get_effective(session), utcnow()


@page.get("/")
def today(request: Request, session: Session = Depends(get_session)):
    eff, now = _eff_now(session)
    d = task_svc.digest_today(session, now)
    sections = {
        k: [sc.task_out(t, eff, now) for t in d[k]]
        for k in ("overdue", "today", "snoozed", "upcoming")
    }
    cats = [c for c, _ in entry_svc.list_categories(session) if c.name_key != "note"]
    return render(request, "today.html", d=d, sections=sections, quick_cats=cats, eff=eff)


@page.post("/quick/task")
async def quick_task(request: Request, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)

    def go(f):
        task_svc.create_task(
            session,
            title=str(f.get("title", "")),
            due_at=parse_local_dt(f.get("due"), eff.tz),
            note=str(f.get("note", "")),
            nag_interval_min=opt_int(f.get("nag_interval_min")),
        )
        return "Task added."

    return await run_action(request, "/", go)


@page.post("/quick/entry")
async def quick_entry(request: Request, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)

    def go(f):
        e = entry_svc.create_entry(
            session,
            category=str(f.get("category", "")),
            text=opt_str(f.get("text")),
            at=parse_local_dt(f.get("at"), eff.tz),
        )
        return f"Logged in {e.category.name}."

    return await run_action(request, "/", go)


@page.get("/tasks")
def tasks_page(
    request: Request,
    status: str = "active",
    q: str = "",
    series_id: int | None = None,
    session: Session = Depends(get_session),
):
    eff, now = _eff_now(session)
    rows = task_svc.list_tasks(
        session, status=status, text=q or None, series_id=series_id, limit=300
    )
    return render(
        request,
        "tasks.html",
        tasks=[sc.task_out(t, eff, now) for t in rows],
        status=status,
        q=q,
        series_id=series_id,
        statuses=["active", "open", "waiting", "snoozed", "done", "skipped", "all"],
        eff=eff,
    )


@page.get("/tasks/{task_id}")
def task_detail(request: Request, task_id: int, session: Session = Depends(get_session)):
    eff, now = _eff_now(session)
    try:
        task = task_svc.get_task(session, task_id)
    except SebastianError as exc:
        flash(request, exc.message, "error")
        return RedirectResponse("/tasks", status_code=303)
    out = sc.task_out(task, eff, now)
    due_local = task.due_at.astimezone(eff.tz).strftime("%Y-%m-%dT%H:%M")
    return render(request, "task_detail.html", t=out, due_local_input=due_local, eff=eff)


@page.post("/tasks/{task_id}/{action}")
async def task_action(
    request: Request, task_id: int, action: str, session: Session = Depends(get_session)
):
    eff = settings_svc.get_effective(session)

    def go(f):
        note = opt_str(f.get("note"))
        if action == "done":
            task_svc.complete_task(session, task_id, note)
            return "Marked done."
        if action == "skip":
            task_svc.skip_task(session, task_id, note)
            return "Skipped."
        if action == "snooze":
            until = parse_local_dt(f.get("until"), eff.tz)
            minutes = opt_int(f.get("minutes")) if until is None else None
            task_svc.snooze_task(session, task_id, until=until, minutes=minutes, note=note)
            return "Snoozed."
        if action == "unsnooze":
            task_svc.unsnooze_task(session, task_id, note)
            return "Snooze cleared."
        if action == "remark":
            task_svc.add_remark(session, task_id, note or "")
            return "Remark added."
        if action == "reopen":
            task_svc.reopen_task(session, task_id, note)
            return "Reopened."
        if action == "edit":
            task_svc.update_task(
                session,
                task_id,
                title=str(f.get("title", "")),
                due_at=parse_local_dt(f.get("due"), eff.tz),
                nag_interval_min=opt_int(f.get("nag_interval_min")),
                quiet_hours=opt_str(f.get("quiet_hours")),
            )
            return "Saved."
        if action == "delete":
            task_svc.delete_task(session, task_id)
            return "Deleted."
        raise SebastianError(f"unknown action {action!r}")

    resp = await run_action(request, f"/tasks/{task_id}", go)
    if action == "delete" and not any(k == "error" for k, _ in request.session.get("flash", [])):
        return RedirectResponse("/tasks", status_code=303)
    return resp


# ---------------------------------------------------------------------- series


@page.get("/series")
def series_page(request: Request, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)
    rows = task_svc.list_series(session, include_inactive=True)
    return render(
        request,
        "series.html",
        series=[sc.series_out(s, eff) for s in rows],
        eff=eff,
        today=local_date(utcnow(), eff.tz).isoformat(),
    )


@page.post("/series/new")
async def series_new(request: Request, session: Session = Depends(get_session)):
    def go(f):
        task_svc.create_series(
            session,
            title=str(f.get("title", "")),
            rrule=str(f.get("rrule", "")),
            note=str(f.get("note", "")),
            dtstart=parse_date(f.get("dtstart")),
            due_time=str(f.get("due_time") or "09:00"),
            nag_interval_min=opt_int(f.get("nag_interval_min")),
            quiet_hours=opt_str(f.get("quiet_hours")),
        )
        return "Series created."

    return await run_action(request, "/series", go)


@page.get("/series/{series_id}")
def series_detail(request: Request, series_id: int, session: Session = Depends(get_session)):
    eff, now = _eff_now(session)
    try:
        s = task_svc.get_series(session, series_id)
    except SebastianError as exc:
        flash(request, exc.message, "error")
        return RedirectResponse("/series", status_code=303)
    rows = task_svc.list_tasks(session, series_id=series_id, limit=100)
    return render(
        request,
        "series_detail.html",
        s=sc.series_out(s, eff),
        tasks=[sc.task_out(t, eff, now) for t in rows],
        eff=eff,
    )


@page.post("/series/{series_id}/{action}")
async def series_action(
    request: Request, series_id: int, action: str, session: Session = Depends(get_session)
):
    def go(f):
        if action == "edit":
            task_svc.update_series(
                session,
                series_id,
                title=str(f.get("title", "")),
                rrule=str(f.get("rrule", "")),
                note=str(f.get("note", "")),
                due_time=str(f.get("due_time") or "09:00"),
                nag_interval_min=opt_int(f.get("nag_interval_min")),
                quiet_hours=opt_str(f.get("quiet_hours")),
            )
            return "Saved."
        if action == "archive":
            task_svc.delete_series(session, series_id)
            return "Series archived."
        if action == "restore":
            task_svc.update_series(session, series_id, active=True)
            return "Series restored."
        raise SebastianError(f"unknown action {action!r}")

    return await run_action(request, f"/series/{series_id}", go)


# --------------------------------------------------------------------- entries


@page.get("/entries")
def entries_page(
    request: Request,
    category: str = "",
    q: str = "",
    start: str = "",
    end: str = "",
    group_by: str = "month",
    session: Session = Depends(get_session),
):
    eff = settings_svc.get_effective(session)
    error = None
    rows, summary = [], None
    try:
        rows = entry_svc.list_entries(
            session,
            category=category or None,
            q=q or None,
            start=parse_date(start),
            end=parse_date(end),
            limit=200,
        )
        summary = entry_svc.summary(
            session,
            category=category or None,
            start=parse_date(start),
            end=parse_date(end),
            group_by=group_by,
        )
    except SebastianError as exc:
        error = exc.message
    cats = [c for c, _ in entry_svc.list_categories(session)]
    return render(
        request,
        "entries.html",
        entries=[sc.entry_out(e, eff) for e in rows],
        summary=summary,
        categories=cats,
        category=category,
        q=q,
        start=start,
        end=end,
        group_by=group_by,
        error=error,
        eff=eff,
    )


@page.post("/entries/new")
async def entry_new(request: Request, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)

    def go(f):
        e = entry_svc.create_entry(
            session,
            category=str(f.get("category", "")),
            text=opt_str(f.get("text")),
            at=parse_local_dt(f.get("at"), eff.tz),
        )
        return f"Recorded in {e.category.name}."

    return await run_action(request, "/entries", go)


@page.post("/entries/{entry_id}/{action}")
async def entry_action(
    request: Request, entry_id: int, action: str, session: Session = Depends(get_session)
):
    eff = settings_svc.get_effective(session)

    def go(f):
        if action == "delete":
            entry_svc.delete_entry(session, entry_id)
            return "Entry deleted."
        if action == "edit":
            entry_svc.update_entry(
                session,
                entry_id,
                category=opt_str(f.get("category")),
                text=opt_str(f.get("text")),
                at=parse_local_dt(f.get("at"), eff.tz),
            )
            return "Entry saved."
        raise SebastianError(f"unknown action {action!r}")

    return await run_action(request, "/entries", go)


# ------------------------------------------------------------------ categories


@page.get("/categories")
def categories_page(request: Request, session: Session = Depends(get_session)):
    rows = entry_svc.list_categories(session, include_archived=True)
    return render(request, "categories.html", cats=[sc.category_out(c, n) for c, n in rows])


@page.post("/categories/new")
async def category_new(request: Request, session: Session = Depends(get_session)):
    def go(f):
        c = entry_svc.create_category(
            session,
            name=str(f.get("name", "")),
            description=str(f.get("description", "")),
            requires_text=f.get("requires_text") == "on",
        )
        return f"Category {c.name!r} created."

    return await run_action(request, "/categories", go)


@page.post("/categories/{cat_id}/{action}")
async def category_action(
    request: Request, cat_id: int, action: str, session: Session = Depends(get_session)
):
    def go(f):
        if action == "edit":
            entry_svc.update_category(
                session,
                cat_id,
                name=str(f.get("name", "")),
                description=str(f.get("description", "")),
                requires_text=f.get("requires_text") == "on",
            )
            return "Category saved."
        if action in ("archive", "restore"):
            entry_svc.update_category(session, cat_id, archived=action == "archive")
            return f"Category {action}d."
        if action == "merge":
            target = entry_svc.get_category(session, str(f.get("into", "")))
            entry_svc.merge_category(session, cat_id, target.id)
            return f"Merged into {target.name!r}."
        raise SebastianError(f"unknown action {action!r}")

    return await run_action(request, "/categories", go)


# -------------------------------------------------------------------- settings


@page.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "settings.html", eff=settings_svc.get_effective(session))


@page.post("/settings")
async def settings_save(request: Request, session: Session = Depends(get_session)):
    def go(f):
        get_tz(str(f.get("timezone", "")))
        settings_svc.update_settings(
            session,
            timezone=opt_str(f.get("timezone")),
            default_nag_interval_min=opt_int(f.get("default_nag_interval_min")),
            quiet_hours=opt_str(f.get("quiet_hours")),
        )
        return "Settings saved."

    return await run_action(request, "/settings", go)


router.include_router(page)
