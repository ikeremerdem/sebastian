from datetime import UTC, datetime

import pytest
from freezegun import freeze_time

from sebastian.services import settings as settings_svc
from sebastian.services import tasks as svc
from sebastian.services.errors import Conflict, Invalid


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def rent(session, **kw):
    return svc.create_series(
        session,
        title="Collect rent",
        rrule="FREQ=MONTHLY;BYMONTHDAY=5",
        due_time="09:00",
        nag_interval_min=120,
        **kw,
    )


@freeze_time("2026-03-01 08:00:00")
def test_series_creates_next_occurrence_as_waiting(session):
    s = rent(session)
    tasks = svc.list_tasks(session)
    assert [t.status for t in tasks] == ["waiting"]
    # 09:00 Berlin (CET, UTC+1) on 5 March
    assert tasks[0].due_at == utc(2026, 3, 5, 8, 0)
    assert tasks[0].series_id == s.id
    assert tasks[0].nag_interval_min == 120


def test_waiting_becomes_open_and_next_month_is_generated(session):
    with freeze_time("2026-03-01 08:00:00"):
        rent(session)
    with freeze_time("2026-03-05 08:00:00"):
        tasks = {t.due_at: t for t in svc.list_tasks(session)}
        assert tasks[utc(2026, 3, 5, 8, 0)].status == "open"
        assert tasks[utc(2026, 4, 5, 7, 0)].status == "waiting"  # CEST from 29 Mar


def test_unfinished_instance_stays_open_when_next_arrives(session):
    with freeze_time("2026-03-01 08:00:00"):
        rent(session)
    with freeze_time("2026-05-06 12:00:00"):
        open_tasks = svc.list_tasks(session, status="open")
        assert len(open_tasks) == 3  # March, April, May all still open
        assert [t.due_at.month for t in open_tasks] == [3, 4, 5]


def test_old_dtstart_does_not_flood_backlog(session):
    with freeze_time("2026-03-10 12:00:00"):
        from datetime import date

        svc.create_series(
            session,
            title="Old",
            rrule="FREQ=MONTHLY;BYMONTHDAY=5",
            dtstart=date(2020, 1, 1),
        )
        tasks = svc.list_tasks(session)
        assert len(tasks) == 1 and tasks[0].status == "waiting"
        assert tasks[0].due_at.month == 4


def test_dst_keeps_local_wall_clock_time(session):
    with freeze_time("2026-03-20 08:00:00"):
        svc.create_series(session, title="Daily", rrule="FREQ=DAILY", due_time="09:00")
    with freeze_time("2026-03-31 20:00:00"):
        tasks = svc.list_tasks(session, status="all")
        by_day = {t.due_at.date().isoformat(): t.due_at for t in tasks}
        # 28 Mar: CET (UTC+1) -> 08:00Z ; 30 Mar: CEST (UTC+2) -> 07:00Z
        assert by_day["2026-03-28"].hour == 8
        assert by_day["2026-03-30"].hour == 7


def test_invalid_rrule_and_times_rejected(session):
    with pytest.raises(Invalid):
        svc.create_series(session, title="x", rrule="FREQ=NOPE")
    with pytest.raises(Invalid):
        svc.create_series(session, title="x", rrule="FREQ=DAILY", due_time="25:00")
    with pytest.raises(Invalid):
        svc.create_series(session, title="x", rrule="FREQ=DAILY", tz="Mars/Base")
    with pytest.raises(Invalid):
        svc.create_series(session, title="x", rrule="DTSTART:20260101T000000\nRRULE:FREQ=DAILY")


@freeze_time("2026-03-05 09:00:00")
def test_nag_cycle_notified_snooze_done(session):
    svc.create_task(
        session, title="Call dentist", due_at=utc(2026, 3, 5, 8, 0), nag_interval_min=30
    )
    t = svc.list_tasks(session)[0]
    assert t.status == "open"

    # first poll: due; repeated polls are idempotent
    assert [(x.id, r) for x, r in svc.get_due(session)] == [(t.id, "due")]
    assert [(x.id, r) for x, r in svc.get_due(session)] == [(t.id, "due")]

    svc.mark_notified(session, t.id)
    assert svc.get_due(session) == []  # suppressed within the interval

    with freeze_time("2026-03-05 09:31:00"):
        assert [(x.id, r) for x, r in svc.get_due(session)] == [(t.id, "nag")]
        svc.snooze_task(session, t.id, minutes=120, note="waiting for bank")
        assert svc.get_due(session) == []

    with freeze_time("2026-03-05 11:32:00"):
        assert len(svc.get_due(session)) == 1  # snooze expired -> nagging resumes
        done = svc.complete_task(session, t.id, note="paid 14:00")
        assert done.status == "done"
        assert svc.get_due(session) == []
        assert "snoozed until 2026-03-05 12:31: waiting for bank" in done.remarks
        assert done.remarks.splitlines()[-1].endswith("done: paid 14:00")
        with pytest.raises(Conflict):
            svc.complete_task(session, t.id)


def test_quiet_hours_defer_nag(session):
    settings_svc.update_settings(session, quiet_hours="22:00-07:00")
    with freeze_time("2026-03-05 21:30:00"):  # 22:30 Berlin -> quiet
        svc.create_task(session, title="x", due_at=utc(2026, 3, 5, 20, 0))
        assert svc.get_due(session) == []
    with freeze_time("2026-03-06 06:30:00"):  # 07:30 Berlin -> allowed
        assert len(svc.get_due(session)) == 1


def test_per_task_quiet_hours_none_overrides_global(session):
    settings_svc.update_settings(session, quiet_hours="22:00-07:00")
    with freeze_time("2026-03-05 21:30:00"):
        svc.create_task(session, title="urgent", due_at=utc(2026, 3, 5, 20, 0), quiet_hours="none")
        assert len(svc.get_due(session)) == 1


@freeze_time("2026-03-05 09:00:00")
def test_skip_and_reopen(session):
    t = svc.create_task(session, title="x", due_at=utc(2026, 3, 5, 8, 0))
    svc.mark_notified(session, t.id)
    svc.skip_task(session, t.id, note="not needed")
    assert svc.get_task(session, t.id).status == "skipped"
    t = svc.reopen_task(session, t.id)
    assert t.status == "open" and t.nag_count == 0 and t.last_notified_at is None


@freeze_time("2026-03-05 09:00:00")
def test_snooze_requires_open_and_future(session):
    t = svc.create_task(session, title="later", due_at=utc(2026, 3, 9, 8, 0))
    with pytest.raises(Conflict):
        svc.snooze_task(session, t.id, minutes=10)
    o = svc.create_task(session, title="now", due_at=utc(2026, 3, 5, 8, 0))
    with pytest.raises(Invalid):
        svc.snooze_task(session, o.id, until=utc(2026, 3, 5, 8, 30))
    with pytest.raises(Invalid):
        svc.snooze_task(session, o.id)


def test_digest_sections(session):
    with freeze_time("2026-03-05 10:00:00"):  # 11:00 Berlin
        overdue = svc.create_task(session, title="overdue", due_at=utc(2026, 3, 3, 8, 0))
        today_open = svc.create_task(session, title="today", due_at=utc(2026, 3, 5, 6, 0))
        today_later = svc.create_task(session, title="later today", due_at=utc(2026, 3, 5, 15, 0))
        snoozed = svc.create_task(session, title="snoozed", due_at=utc(2026, 3, 4, 8, 0))
        svc.snooze_task(session, snoozed.id, minutes=600)
        soon = svc.create_task(session, title="soon", due_at=utc(2026, 3, 8, 8, 0))
        far = svc.create_task(session, title="far", due_at=utc(2026, 4, 8, 8, 0))
        d = svc.digest_today(session)
        ids = lambda k: {t.id for t in d[k]}
        assert ids("overdue") == {overdue.id}
        assert ids("today") == {today_open.id, today_later.id}
        assert ids("snoozed") == {snoozed.id}
        assert ids("upcoming") == {soon.id}
        assert far.id not in ids("upcoming")


def test_series_update_propagates_and_archive_keeps_history(session):
    with freeze_time("2026-03-01 08:00:00"):
        s = rent(session)
        svc.update_series(session, s.id, title="Rent from tenant", nag_interval_min=45)
        t = svc.list_tasks(session)[0]
        assert t.title == "Rent from tenant" and t.nag_interval_min == 45
    with freeze_time("2026-03-06 08:00:00"):
        svc.delete_series(session, s.id)
        remaining = svc.list_tasks(session)
        assert [x.status for x in remaining] == ["open"]  # history kept, waiting removed
    with freeze_time("2026-05-01 08:00:00"):
        assert len(svc.list_tasks(session)) == 1  # archived series generates nothing


def test_schedule_change_regenerates_future_only(session):
    with freeze_time("2026-03-01 08:00:00"):
        s = rent(session)
        svc.update_series(session, s.id, rrule="FREQ=MONTHLY;BYMONTHDAY=10")
        tasks = svc.list_tasks(session)
        assert len(tasks) == 1 and tasks[0].due_at.day == 10


def test_generated_tasks_cannot_be_deleted_but_oneoffs_can(session):
    with freeze_time("2026-03-01 08:00:00"):
        rent(session)
        gen = svc.list_tasks(session)[0]
        with pytest.raises(Conflict):
            svc.delete_task(session, gen.id)
        one = svc.create_task(session, title="tmp")
        svc.delete_task(session, one.id)


def test_end_of_month_rule(session):
    with freeze_time("2026-02-01 08:00:00"):
        svc.create_series(session, title="Month end", rrule="FREQ=MONTHLY;BYMONTHDAY=-1")
        t = svc.list_tasks(session)[0]
        assert t.due_at == utc(2026, 2, 28, 8, 0)  # 09:00 CET on the last day
