"""Categories and entries (notes + logs), search and summaries."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..clock import utcnow
from ..models import Category, Entry
from . import settings as settings_svc
from .errors import Conflict, Invalid, NotFound
from .timeutil import local_date, local_midnight_utc, week_start

# ------------------------------------------------------------------ categories


class CategoryError(Invalid):
    """Unknown/archived category; carries the valid choices so the agent can retry."""


def _key(name: str) -> str:
    return name.strip().casefold()


def _valid_categories(session: Session) -> list[dict]:
    rows = session.scalars(
        select(Category).where(Category.archived.is_(False)).order_by(Category.name)
    )
    return [{"name": c.name, "description": c.description} for c in rows]


def create_category(
    session: Session, *, name: str, description: str = "", requires_text: bool = False
) -> Category:
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise Invalid("category name must be 1-80 characters")
    if session.scalar(select(Category).where(Category.name_key == _key(name))):
        raise Conflict(f"category {name!r} already exists")
    cat = Category(
        name=name,
        name_key=_key(name),
        description=description or "",
        requires_text=requires_text,
    )
    session.add(cat)
    session.commit()
    return cat


def get_category(session: Session, ref: int | str) -> Category:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        cat = session.get(Category, int(ref))
    else:
        cat = session.scalar(select(Category).where(Category.name_key == _key(ref)))
    if cat is None:
        raise NotFound(f"category {ref!r} not found")
    return cat


def resolve_category(session: Session, ref: int | str) -> Category:
    """Active category by name/id, or CategoryError listing the valid ones."""
    try:
        cat = get_category(session, ref)
    except NotFound:
        cat = None
    if cat is None or cat.archived:
        raise CategoryError(
            f"unknown or archived category {ref!r}",
            valid_categories=_valid_categories(session),
        )
    return cat


def update_category(session: Session, ref: int | str, **changes) -> Category:
    cat = get_category(session, ref)
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if not name or len(name) > 80:
            raise Invalid("category name must be 1-80 characters")
        clash = session.scalar(
            select(Category).where(Category.name_key == _key(name), Category.id != cat.id)
        )
        if clash:
            raise Conflict(f"category {name!r} already exists")
        cat.name, cat.name_key = name, _key(name)
    for key in ("description", "requires_text", "archived"):
        if changes.get(key) is not None:
            setattr(cat, key, changes[key])
    session.commit()
    return cat


def merge_category(session: Session, source: int | str, target: int | str) -> Category:
    src, dst = get_category(session, source), get_category(session, target)
    if src.id == dst.id:
        raise Invalid("source and target are the same category")
    for entry in session.scalars(select(Entry).where(Entry.category_id == src.id)):
        entry.category_id = dst.id
    session.delete(src)
    session.commit()
    return dst


def list_categories(session: Session, include_archived: bool = False) -> list[tuple[Category, int]]:
    q = (
        select(Category, func.count(Entry.id))
        .outerjoin(Entry, Entry.category_id == Category.id)
        .group_by(Category.id)
        .order_by(Category.name)
    )
    if not include_archived:
        q = q.where(Category.archived.is_(False))
    return [(c, n) for c, n in session.execute(q).all()]


# --------------------------------------------------------------------- entries


def create_entry(
    session: Session,
    *,
    category: int | str,
    text: str | None = None,
    at: datetime | None = None,
) -> Entry:
    cat = resolve_category(session, category)
    text = _clean_text(text, cat)
    if at is not None and at.tzinfo is None:
        raise Invalid("'at' must include a timezone offset")
    entry = Entry(category_id=cat.id, text=text, created_at=at or utcnow())
    session.add(entry)
    session.commit()
    entry.category = cat
    return entry


def get_entry(session: Session, entry_id: int) -> Entry:
    entry = session.scalars(
        select(Entry).options(joinedload(Entry.category)).where(Entry.id == entry_id)
    ).first()
    if entry is None:
        raise NotFound(f"entry {entry_id} not found")
    return entry


def update_entry(session: Session, entry_id: int, **changes) -> Entry:
    entry = get_entry(session, entry_id)
    if changes.get("category") is not None:
        entry.category = resolve_category(session, changes["category"])
    if "text" in changes:
        entry.text = _clean_text(changes["text"], entry.category)
    elif entry.category.requires_text and not entry.text:
        raise Invalid(f"category {entry.category.name!r} requires text")
    if changes.get("at") is not None:
        if changes["at"].tzinfo is None:
            raise Invalid("'at' must include a timezone offset")
        entry.created_at = changes["at"]
    session.commit()
    return entry


def delete_entry(session: Session, entry_id: int) -> None:
    session.delete(get_entry(session, entry_id))
    session.commit()


def list_entries(
    session: Session,
    *,
    category: int | str | None = None,
    q: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Entry]:
    eff = settings_svc.get_effective(session)
    query = select(Entry).options(joinedload(Entry.category))
    if category is not None:
        query = query.where(Entry.category_id == get_category(session, category).id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(Entry.text.ilike(like))
    if start is not None:
        query = query.where(Entry.created_at >= local_midnight_utc(start, eff.tz))
    if end is not None:
        query = query.where(Entry.created_at < local_midnight_utc(end + timedelta(days=1), eff.tz))
    return list(
        session.scalars(
            query.order_by(Entry.created_at.desc(), Entry.id.desc()).limit(limit).offset(offset)
        )
    )


def _clean_text(text: str | None, cat: Category) -> str | None:
    text = text.strip() if text else None
    if cat.requires_text and not text:
        raise Invalid(f"category {cat.name!r} requires text")
    return text or None


# -------------------------------------------------------------------- summaries


def _period_key(d: date, group_by: str) -> tuple[str, date]:
    if group_by == "month":
        return d.strftime("%Y-%m"), d.replace(day=1)
    if group_by == "year":
        return d.strftime("%Y"), d.replace(month=1, day=1)
    if group_by == "week":
        iso = d.isocalendar()
        return f"{iso.year}-W{iso.week:02d}", week_start(d)
    return "all", date.min


def summary(
    session: Session,
    *,
    category: int | str | None = None,
    start: date | None = None,
    end: date | None = None,
    group_by: str = "month",
) -> dict:
    """Counts and distinct local days per category (and period). Dates inclusive, local tz."""
    if group_by not in ("week", "month", "year", "none"):
        raise Invalid("group_by must be one of week, month, year, none")
    eff = settings_svc.get_effective(session)
    tz = eff.tz
    today = local_date(utcnow(), tz)
    start = start or date(today.year, 1, 1)
    end = end or today
    if end < start:
        raise Invalid("'to' must not be before 'from'")

    q = select(Entry.category_id, Entry.created_at).where(
        Entry.created_at >= local_midnight_utc(start, tz),
        Entry.created_at < local_midnight_utc(end + timedelta(days=1), tz),
    )
    cats = {c.id: c for c, _ in list_categories(session, include_archived=True)}
    if category is not None:
        wanted = get_category(session, category)
        q = q.where(Entry.category_id == wanted.id)
        cats = {wanted.id: wanted}

    totals: dict[int, list] = defaultdict(lambda: [0, set()])
    periods: dict[int, dict[str, list]] = defaultdict(dict)
    for cat_id, created in session.execute(q):
        d = local_date(created, tz)
        totals[cat_id][0] += 1
        totals[cat_id][1].add(d)
        key, pstart = _period_key(d, group_by)
        slot = periods[cat_id].setdefault(key, [pstart, 0, set()])
        slot[1] += 1
        slot[2].add(d)

    out = []
    for cat_id, cat in sorted(cats.items(), key=lambda kv: kv[1].name.casefold()):
        if category is None and cat_id not in totals:
            continue
        total, days = totals.get(cat_id, [0, set()])
        out.append(
            {
                "category": cat.name,
                "total_entries": total,
                "distinct_days": len(days),
                "periods": [
                    {
                        "period": key,
                        "period_start": None if group_by == "none" else pstart.isoformat(),
                        "entries": n,
                        "distinct_days": len(ds),
                    }
                    for key, (pstart, n, ds) in sorted(
                        periods.get(cat_id, {}).items(), key=lambda kv: kv[1][0]
                    )
                ],
            }
        )
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "group_by": group_by,
        "timezone": eff.timezone,
        "categories": out,
    }


def period_range(period: str, today: date) -> tuple[date, date]:
    if period == "week":
        return week_start(today), today
    if period == "last_week":
        s = week_start(today) - timedelta(days=7)
        return s, s + timedelta(days=6)
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "month":
        return today.replace(day=1), today
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if period == "year":
        return date(today.year, 1, 1), today
    raise Invalid("period must be one of week, last_week, last_7_days, month, last_month, year")


def digest_entries(session: Session, period: str = "week") -> dict:
    eff = settings_svc.get_effective(session)
    start, end = period_range(period, local_date(utcnow(), eff.tz))
    result = summary(session, start=start, end=end, group_by="none")
    notes = (
        list_entries(session, category="Note", start=start, end=end, limit=50)
        if _has_note(session)
        else []
    )
    result["period"] = period
    result["notes"] = notes
    return result


def _has_note(session: Session) -> bool:
    return session.scalar(select(Category.id).where(Category.name_key == "note")) is not None
