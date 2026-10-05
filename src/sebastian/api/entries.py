from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import schemas as sc
from ..services import entries as svc
from ..services import settings as settings_svc
from .deps import get_session

router = APIRouter()


@router.get("/categories", tags=["categories"])
def list_categories(include_archived: bool = False, session: Session = Depends(get_session)):
    """Names + descriptions double as interpretation context for the agent."""
    return [sc.category_out(c, n) for c, n in svc.list_categories(session, include_archived)]


@router.post("/categories", status_code=201, tags=["categories"])
def create_category(body: sc.CategoryCreate, session: Session = Depends(get_session)):
    return sc.category_out(svc.create_category(session, **body.model_dump()))


@router.patch("/categories/{ref}", tags=["categories"])
def update_category(ref: str, body: sc.CategoryUpdate, session: Session = Depends(get_session)):
    return sc.category_out(svc.update_category(session, ref, **body.model_dump(exclude_unset=True)))


@router.post("/categories/{ref}/merge", tags=["categories"])
def merge_category(ref: str, body: sc.MergeBody, session: Session = Depends(get_session)):
    """Moves all entries of {ref} into the target category and deletes {ref}."""
    return sc.category_out(svc.merge_category(session, ref, body.into))


@router.post("/entries", status_code=201, tags=["entries"])
def create_entry(body: sc.EntryCreate, session: Session = Depends(get_session)):
    eff = settings_svc.get_effective(session)
    return sc.entry_out(svc.create_entry(session, **body.model_dump()), eff)


@router.get("/entries", tags=["entries"])
def list_entries(
    category: str | None = None,
    q: str | None = Query(None, description="text search"),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    eff = settings_svc.get_effective(session)
    rows = svc.list_entries(
        session, category=category, q=q, start=start, end=end, limit=limit, offset=offset
    )
    return [sc.entry_out(e, eff) for e in rows]


@router.get("/entries/summary", tags=["entries"])
def entries_summary(
    category: str | None = None,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    group_by: str = Query("month", description="week|month|year|none"),
    session: Session = Depends(get_session),
):
    """Entries and distinct local days per category and period (default: this year)."""
    return svc.summary(session, category=category, start=start, end=end, group_by=group_by)


@router.get("/digest/entries", tags=["digest"])
def digest_entries(
    period: str = Query("week", description="week|last_week|last_7_days|month|last_month|year"),
    session: Session = Depends(get_session),
):
    eff = settings_svc.get_effective(session)
    d = svc.digest_entries(session, period)
    d["notes"] = [sc.entry_out(e, eff) for e in d["notes"]]
    return d


@router.get("/entries/{entry_id}", tags=["entries"])
def get_entry(entry_id: int, session: Session = Depends(get_session)):
    return sc.entry_out(svc.get_entry(session, entry_id), settings_svc.get_effective(session))


@router.patch("/entries/{entry_id}", tags=["entries"])
def update_entry(entry_id: int, body: sc.EntryUpdate, session: Session = Depends(get_session)):
    entry = svc.update_entry(session, entry_id, **body.model_dump(exclude_unset=True))
    return sc.entry_out(entry, settings_svc.get_effective(session))


@router.delete("/entries/{entry_id}", status_code=204, tags=["entries"])
def delete_entry(entry_id: int, session: Session = Depends(get_session)):
    svc.delete_entry(session, entry_id)
