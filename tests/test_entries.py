from datetime import UTC, date, datetime

import pytest
from freezegun import freeze_time

from sebastian.services import entries as svc
from sebastian.services.errors import Conflict, Invalid, NotFound


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_note_category_is_seeded_and_requires_text(session):
    cats = {c.name: c for c, _ in svc.list_categories(session)}
    assert cats["Note"].requires_text is True
    with pytest.raises(Invalid):
        svc.create_entry(session, category="Note")
    e = svc.create_entry(session, category="note", text="  pizza 18 EUR ")
    assert e.text == "pizza 18 EUR" and e.category.name == "Note"


def test_unknown_category_lists_valid_ones(session):
    svc.create_category(session, name="Public Transport", description="count each trip")
    with pytest.raises(svc.CategoryError) as exc:
        svc.create_entry(session, category="transit")
    names = [c["name"] for c in exc.value.extra["valid_categories"]]
    assert names == ["Note", "Public Transport"]
    assert exc.value.extra["valid_categories"][1]["description"] == "count each trip"


def test_category_names_case_insensitive_and_unique(session):
    svc.create_category(session, name="Public Transport")
    with pytest.raises(Conflict):
        svc.create_category(session, name="public transport")
    e = svc.create_entry(session, category="PUBLIC TRANSPORT")
    assert e.text is None and e.category.name == "Public Transport"


def test_archived_category_rejected_and_rename_keeps_history(session):
    c = svc.create_category(session, name="Bus")
    e = svc.create_entry(session, category="Bus")
    svc.update_category(session, c.id, name="Public Transport")
    assert svc.get_entry(session, e.id).category.name == "Public Transport"
    svc.update_category(session, c.id, archived=True)
    with pytest.raises(svc.CategoryError):
        svc.create_entry(session, category="Public Transport")


def test_merge_category_reassigns_entries(session):
    a = svc.create_category(session, name="Bus")
    b = svc.create_category(session, name="Tram")
    svc.create_entry(session, category="Bus")
    svc.create_entry(session, category="Tram")
    svc.merge_category(session, a.id, b.id)
    assert [(c.name, n) for c, n in svc.list_categories(session) if c.name == "Tram"] == [
        ("Tram", 2)
    ]
    with pytest.raises(NotFound):
        svc.get_category(session, "Bus")


def test_search_filters(session):
    svc.create_entry(session, category="Note", text="Pizza Joint 18 EUR")
    svc.create_entry(session, category="Note", text="dentist moved to friday")
    assert [e.text for e in svc.list_entries(session, q="pizza")] == ["Pizza Joint 18 EUR"]


def test_summary_counts_and_distinct_days_in_local_timezone(session):
    svc.create_category(session, name="Public Transport")
    at = lambda *a: svc.create_entry(session, category="Public Transport", at=utc(*a))
    at(2026, 1, 5, 7, 0)
    at(2026, 1, 5, 16, 0)  # same day -> 2 entries, 1 distinct day
    at(2026, 1, 6, 8, 0)
    at(2026, 2, 3, 8, 0)
    # 23:30Z on 31 Jan is 00:30 on 1 Feb in Berlin -> counts for February
    at(2026, 1, 31, 23, 30)
    with freeze_time("2026-03-01 12:00:00"):
        res = svc.summary(session, category="Public Transport", group_by="month")
    cat = res["categories"][0]
    assert cat["total_entries"] == 5 and cat["distinct_days"] == 4
    by = {p["period"]: (p["entries"], p["distinct_days"]) for p in cat["periods"]}
    assert by == {"2026-01": (3, 2), "2026-02": (2, 2)}


def test_summary_year_and_range_and_empty_category(session):
    svc.create_category(session, name="Gym")
    svc.create_entry(session, category="Gym", at=utc(2025, 12, 31, 12, 0))
    svc.create_entry(session, category="Gym", at=utc(2026, 1, 2, 12, 0))
    with freeze_time("2026-03-01 12:00:00"):
        y = svc.summary(session, category="Gym", group_by="year", start=date(2025, 1, 1))
        assert [(p["period"], p["entries"]) for p in y["categories"][0]["periods"]] == [
            ("2025", 1),
            ("2026", 1),
        ]
        none = svc.summary(session, category="Gym", start=date(2026, 2, 1), group_by="none")
        assert none["categories"][0]["total_entries"] == 0


def test_digest_periods(session):
    svc.create_category(session, name="Gym")
    for day in (5, 6, 12):
        svc.create_entry(session, category="Gym", at=utc(2026, 3, day, 12, 0))
    svc.create_entry(session, category="Note", text="hello", at=utc(2026, 3, 12, 12, 0))
    with freeze_time("2026-03-13 12:00:00"):  # Friday
        wk = svc.digest_entries(session, "week")  # Mon 9 .. Fri 13
        assert wk["from"] == "2026-03-09"
        gym = next(c for c in wk["categories"] if c["category"] == "Gym")
        assert gym["total_entries"] == 1
        assert [n.text for n in wk["notes"]] == ["hello"]
        last = svc.digest_entries(session, "last_week")  # 2-8 March
        assert (last["from"], last["to"]) == ("2026-03-02", "2026-03-08")
        assert next(c for c in last["categories"] if c["category"] == "Gym")["total_entries"] == 2
    with pytest.raises(Invalid):
        svc.digest_entries(session, "fortnight")
