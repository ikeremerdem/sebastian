"""MCP adapter: agent-friendly tools over the same service layer as the REST API."""

import json
from datetime import date, datetime
from typing import Literal

from fastapi.encoders import jsonable_encoder
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from . import schemas as sc
from .auth import check_api_key
from .clock import utcnow
from .db import Database
from .services import entries as entry_svc
from .services import settings as settings_svc
from .services import tasks as task_svc
from .services.errors import SebastianError

INSTRUCTIONS = """\
Sebastian is the user's personal task, reminder, log and note store. It is passive:
you (the agent) poll it, deliver messages to the user yourself, and report back.

TASKS. A task has a due time and nags until the user explicitly says it is done.
Recurring obligations (monthly rent, bills) are a *series* that generates one task per
occurrence; one-off tasks are plain tasks. Only complete_task / skip_task stop nagging;
snoozing only pauses it. Every task has `remarks`, a timestamped history of notes.
NAG LOOP: call get_due periodically. For each item, message the user, then call
mark_notified(task_id) so the next nag respects the interval. When the user says a task
is done, call complete_task (pass any remark they gave). Never invent task ids: use
list_tasks(title_contains=...) to find them.

ENTRIES. Notes and counted occurrences ("record public transport") are entries in a
category. Call list_categories first and use the descriptions to pick the right category.
Categories are enforced; if none fits, ask the user before create_category.
Plain notes go in the "Note" category with text.

All times are ISO 8601; naive times are rejected, always include the UTC offset. Dates in
summaries are local calendar days in the user's timezone (see get_settings).
"""


def _fail(exc: SebastianError) -> ToolError:
    """Anticipated failures reach the model verbatim (incl. e.g. the valid categories)."""
    detail = (
        f"{exc.message}. {json.dumps(jsonable_encoder(exc.extra))}" if exc.extra else exc.message
    )
    return ToolError(detail)


def build_mcp(db: Database) -> MCPServer:
    mcp = MCPServer("sebastian", instructions=INSTRUCTIONS)

    def run(fn):
        """Run fn(session, eff, now) -> data; convert domain errors to tool errors."""
        gen = db.session()
        session = next(gen)
        try:
            eff = settings_svc.get_effective(session)
            return jsonable_encoder(fn(session, eff, utcnow()))
        except SebastianError as exc:
            raise _fail(exc) from exc
        finally:
            session.close()

    # ---------------------------------------------------------------- nagging

    @mcp.tool()
    def get_due() -> dict:
        """Tasks to remind/nag the user about right now. Read-only: after you actually
        message the user about an item, call mark_notified(task_id). `reason` is
        due (first reminder), nag (repeat) or overdue (past its due day)."""

        def go(session, eff, now):
            items = []
            for task, reason in task_svc.get_due(session, now):
                item = sc.task_out(task, eff, now)
                item.update(reason=reason, nag_number=task.nag_count + 1)
                items.append(item)
            return {"now": now, "timezone": eff.timezone, "count": len(items), "items": items}

        return run(go)

    @mcp.tool()
    def mark_notified(task_id: int) -> dict:
        """Record that you just delivered a reminder for this task (starts the next interval)."""
        return run(lambda s, e, n: sc.task_out(task_svc.mark_notified(s, task_id), e, n))

    @mcp.tool()
    def get_digest_today(upcoming_days: int = 7) -> dict:
        """Morning brief data: tasks due today, overdue, currently snoozed (with wake-up
        times; always mention these so nothing is forgotten) and upcoming."""

        def go(session, eff, now):
            d = task_svc.digest_today(session, now, upcoming_days)
            out = {"date": d["date"], "timezone": d["timezone"]}
            for key in ("today", "overdue", "snoozed", "upcoming"):
                out[key] = [sc.task_out(t, eff, now) for t in d[key]]
            return out

        return run(go)

    # ------------------------------------------------------------------ tasks

    @mcp.tool()
    def list_tasks(
        status: Literal[
            "active", "open", "waiting", "snoozed", "done", "skipped", "all"
        ] = "active",
        title_contains: str | None = None,
        series_id: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List tasks. 'active' = waiting + open. Each recurring task shows its `occurrence`
        date (e.g. rent of 2026-03-05 vs 2026-04-05) and its `remarks` history."""

        def go(session, eff, now):
            rows = task_svc.list_tasks(
                session, status=status, series_id=series_id, text=title_contains, limit=limit
            )
            return [sc.task_out(t, eff, now) for t in rows]

        return run(go)

    @mcp.tool()
    def create_task(
        title: str,
        due_at: datetime | None = None,
        note: str = "",
        nag_interval_min: int | None = None,
        quiet_hours: str | None = None,
    ) -> dict:
        """Create a one-off task that nags from due_at (default: now) until completed.
        due_at needs a UTC offset. quiet_hours like '22:00-07:00' or 'none'."""
        return run(
            lambda s, e, n: sc.task_out(
                task_svc.create_task(
                    s,
                    title=title,
                    due_at=due_at,
                    note=note,
                    nag_interval_min=nag_interval_min,
                    quiet_hours=quiet_hours,
                ),
                e,
                n,
            )
        )

    @mcp.tool()
    def complete_task(task_id: int, note: str | None = None) -> dict:
        """The user says this task is done. Stops nagging; recurring series continue with
        their next occurrence on schedule. Optional remark is stored in the history."""
        return run(lambda s, e, n: sc.task_out(task_svc.complete_task(s, task_id, note), e, n))

    @mcp.tool()
    def snooze_task(
        task_id: int,
        minutes: int | None = None,
        until: datetime | None = None,
        note: str | None = None,
    ) -> dict:
        """Pause nagging for N minutes or until a time (give exactly one). It resumes
        automatically afterwards; only completing/skipping stops it for good."""
        return run(
            lambda s, e, n: sc.task_out(
                task_svc.snooze_task(s, task_id, until=until, minutes=minutes, note=note), e, n
            )
        )

    @mcp.tool()
    def skip_task(task_id: int, note: str | None = None) -> dict:
        """Close a task/occurrence without doing it (e.g. 'no rent this month'). Stops nagging."""
        return run(lambda s, e, n: sc.task_out(task_svc.skip_task(s, task_id, note), e, n))

    @mcp.tool()
    def add_task_remark(task_id: int, note: str) -> dict:
        """Append a timestamped remark to a task's history."""
        return run(lambda s, e, n: sc.task_out(task_svc.add_remark(s, task_id, note), e, n))

    @mcp.tool()
    def reopen_task(task_id: int, note: str | None = None) -> dict:
        """Undo a completion/skip (e.g. marked done by mistake)."""
        return run(lambda s, e, n: sc.task_out(task_svc.reopen_task(s, task_id, note), e, n))

    # ----------------------------------------------------------------- series

    @mcp.tool()
    def create_series(
        title: str,
        rrule: str,
        note: str = "",
        due_time: str = "09:00",
        dtstart: date | None = None,
        nag_interval_min: int | None = None,
        quiet_hours: str | None = None,
    ) -> dict:
        """Create a recurring task. rrule is an iCalendar RRULE without DTSTART, e.g.
        'FREQ=MONTHLY;BYMONTHDAY=5', 'FREQ=MONTHLY;BYMONTHDAY=-1' (last day),
        'FREQ=WEEKLY;BYDAY=MO,TH'. due_time is local HH:MM. Each occurrence nags until done.
        `note` is shown with every occurrence (e.g. amount, account)."""

        def go(session, eff, now):
            series = task_svc.create_series(
                session,
                title=title,
                rrule=rrule,
                note=note,
                due_time=due_time,
                dtstart=dtstart,
                nag_interval_min=nag_interval_min,
                quiet_hours=quiet_hours,
            )
            return sc.series_out(series, eff)

        return run(go)

    @mcp.tool()
    def list_series() -> list[dict]:
        """All active recurring series."""
        return run(lambda s, e, n: [sc.series_out(x, e) for x in task_svc.list_series(s)])

    @mcp.tool()
    def archive_series(series_id: int) -> dict:
        """Stop a recurring series (history is kept)."""

        def go(session, eff, now):
            task_svc.delete_series(session, series_id)
            return {"archived": series_id}

        return run(go)

    # ---------------------------------------------------------------- entries

    @mcp.tool()
    def list_categories() -> list[dict]:
        """Entry categories with descriptions (use them to interpret the user's messages)
        and entry counts. Call this before add_entry."""
        return run(lambda s, e, n: [sc.category_out(c, k) for c, k in entry_svc.list_categories(s)])

    @mcp.tool()
    def create_category(name: str, description: str = "", requires_text: bool = False) -> dict:
        """Create a new entry category. Only do this with the user's agreement."""
        return run(
            lambda s, e, n: sc.category_out(
                entry_svc.create_category(
                    s, name=name, description=description, requires_text=requires_text
                )
            )
        )

    @mcp.tool()
    def add_entry(category: str, text: str | None = None, at: datetime | None = None) -> dict:
        """Record an entry now (or at `at`, with UTC offset). 'Record public transport' =
        category 'Public Transport' without text. Notes: category 'Note' with the user's
        text verbatim. Unknown categories are rejected with the list of valid ones."""
        return run(
            lambda s, e, n: sc.entry_out(
                entry_svc.create_entry(s, category=category, text=text, at=at), e
            )
        )

    @mcp.tool()
    def search_entries(
        query: str | None = None,
        category: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Find entries/notes by text, category and local-date range (inclusive), newest first."""
        return run(
            lambda s, e, n: [
                sc.entry_out(x, e)
                for x in entry_svc.list_entries(
                    s, category=category, q=query, start=from_date, end=to_date, limit=limit
                )
            ]
        )

    @mcp.tool()
    def entry_summary(
        category: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        group_by: Literal["week", "month", "year", "none"] = "month",
    ) -> dict:
        """Counts per category and period: total `entries` and `distinct_days` (local
        calendar days with at least one entry). Default range: this year to date.
        Use for 'how many times per month did I take public transport?'."""
        return run(
            lambda s, e, n: entry_svc.summary(
                s, category=category, start=from_date, end=to_date, group_by=group_by
            )
        )

    @mcp.tool()
    def get_digest_entries(
        period: Literal["week", "last_week", "last_7_days", "month", "last_month", "year"] = "week",
    ) -> dict:
        """Summary of entries for a period (e.g. weekly log summary), plus the notes written."""

        def go(session, eff, now):
            d = entry_svc.digest_entries(session, period)
            d["notes"] = [sc.entry_out(x, eff) for x in d["notes"]]
            return d

        return run(go)

    @mcp.tool()
    def get_settings() -> dict:
        """The user's timezone, default nag interval and quiet hours."""
        return run(lambda s, e, n: e.as_dict())

    return mcp


class BearerAuthMiddleware:
    """Pure-ASGI guard for the /mcp endpoint (other paths fall through to 404)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            headers = dict(scope["headers"])
            token = headers.get(b"authorization", b"").decode()
            if not check_api_key(token):
                response = JSONResponse(
                    {"detail": "missing or invalid bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def mcp_asgi_app(mcp: MCPServer):
    # Bearer auth protects the endpoint, and we bind to localhost, so Host-header
    # rebinding protection would only get in the way of tunnels / reverse proxies.
    inner = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    return BearerAuthMiddleware(inner)
