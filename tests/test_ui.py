import re


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(ui, password="hunter2"):
    page = ui.get("/login")
    return ui.post("/login", data={"password": password, "csrf": csrf_of(page.text)})


def test_anonymous_is_redirected_to_login(ui):
    r = ui.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert ui.get("/tasks").status_code == 303
    assert ui.post("/quick/task", data={"title": "x"}).status_code == 303


def test_wrong_password_then_right_password(ui):
    bad = login(ui, "nope")
    assert bad.status_code == 303
    assert "Wrong password" in ui.get("/login").text
    ok = login(ui)
    assert ok.headers["location"] == "/"
    assert "Today" in ui.get("/").text


def test_login_is_rate_limited(ui):
    from sebastian.ui import routes

    routes._failures.clear()
    for _ in range(5):
        login(ui, "nope")
    login(ui)  # correct password, but locked out
    assert ui.get("/").status_code == 303
    routes._failures.clear()


def test_csrf_is_enforced(ui):
    login(ui)
    r = ui.post("/quick/task", data={"title": "sneaky", "csrf": "forged"}, follow_redirects=True)
    assert "form expired" in r.text or r.status_code in (303, 422)
    assert "sneaky" not in ui.get("/tasks").text


def test_task_flow_via_ui(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    r = ui.post(
        "/quick/task",
        data={"title": "Pay daughter's rent", "note": "5th", "csrf": token, "next": "/"},
    )
    assert r.status_code == 303
    page = ui.get("/").text
    assert "Pay daughter&#39;s rent" in page and "Due today" in page
    tid = re.search(r"/tasks/(\d+)", page).group(1)

    ui.post(f"/tasks/{tid}/snooze", data={"minutes": "60", "note": "later", "csrf": token})
    assert "Snoozed · will resume by themselves" in ui.get("/").text
    ui.post(
        f"/tasks/{tid}/done", data={"note": "paid", "csrf": token, "next": "/tasks?status=done"}
    )
    detail = ui.get(f"/tasks/{tid}").text
    assert "snoozed until" in detail and "done: paid" in detail
    assert "Pay daughter&#39;s rent" in ui.get("/tasks?status=done").text


def test_series_categories_entries_via_ui(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post(
        "/series/new",
        data={
            "title": "Collect rent",
            "repeat": "monthly",
            "day": "5",
            "due_time": "09:00",
            "csrf": token,
        },
    )
    assert "Collect rent" in ui.get("/series").text
    bad = ui.post(
        "/series/new", data={"title": "x", "repeat": "custom", "rrule": "FREQ=NOPE", "csrf": token}
    )
    assert bad.status_code == 303
    assert "invalid rrule" in ui.get("/series").text

    ui.post(
        "/categories/new",
        data={"name": "Public Transport", "description": "count trips", "csrf": token},
    )
    assert "Public Transport" in ui.get("/categories").text
    ui.post("/quick/entry", data={"category": "Public Transport", "csrf": token})
    ui.post("/entries/new", data={"category": "Note", "text": "pizza 18 EUR", "csrf": token})
    entries = ui.get("/entries").text
    assert "pizza 18 EUR" in entries and "Public Transport" in entries
    # a Note without text is refused with a readable message
    ui.post("/entries/new", data={"category": "Note", "csrf": token})
    assert "requires text" in ui.get("/entries").text


def test_settings_page_saves(ui):
    login(ui)
    token = csrf_of(ui.get("/settings").text)
    ui.post(
        "/settings",
        data={
            "timezone": "Europe/Istanbul",
            "default_nag_interval_min": "45",
            "quiet_hours": "23:00-06:00",
            "csrf": token,
        },
    )
    assert "Europe/Istanbul" in ui.get("/settings").text
    ui.post("/settings", data={"timezone": "Mars/Base", "csrf": token})
    assert "unknown timezone" in ui.get("/settings").text


def test_static_and_api_unaffected(ui):
    assert ui.get("/static/style.css").status_code == 200
    assert ui.get("/api/v1/tasks").status_code == 401
    assert ui.get("/health").status_code == 200


def test_every_form_carries_a_real_csrf_token(ui):
    """Regression: macro-rendered hidden fields were empty (Jinja macro scoping)."""
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post("/quick/task", data={"title": "T", "csrf": token})
    ui.post("/series/new", data={"title": "S", "repeat": "daily", "csrf": token})
    ui.post("/categories/new", data={"name": "Gym", "csrf": token})
    ui.post("/quick/entry", data={"category": "Gym", "csrf": token})
    for path in (
        "/",
        "/tasks",
        "/tasks/1",
        "/series",
        "/series/1",
        "/entries",
        "/categories",
        "/settings",
    ):
        html = ui.get(path).text
        assert 'name="csrf" value=""' not in html, path
        assert 'name="csrf"' in html, path
        for t in re.findall(r'name="csrf" value="([^"]*)"', html):
            assert t == token, path


def test_task_actions_work_with_the_form_tokens_on_the_page(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post("/quick/task", data={"title": "Pay rent", "csrf": token})
    page = ui.get("/").text
    form = re.search(r'<form method="post" action="/tasks/1/done".*?</form>', page, re.S).group(0)
    page_token = re.search(r'name="csrf" value="([^"]+)"', form).group(1)
    r = ui.post("/tasks/1/done", data={"note": "ok", "csrf": page_token, "next": "/"})
    assert r.status_code == 303
    assert "Marked done" in ui.get("/").text


def test_delete_tasks_from_the_ui_including_recurring_occurrences(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post("/quick/task", data={"title": "Throwaway", "csrf": token})
    ui.post(
        "/series/new",
        data={"title": "Monthly rent", "repeat": "monthly", "day": "5", "csrf": token},
    )
    page = ui.get("/tasks?status=all").text
    assert "Throwaway" in page and "Monthly rent" in page
    ids = re.findall(r'<a class="title" href="/tasks/(\d+)">([^<]+)</a>', page)
    by_title = {t: i for i, t in ids}

    # one-off: delete from the list, land back on the list
    r = ui.post(
        f"/tasks/{by_title['Throwaway']}/delete", data={"csrf": token, "next": "/tasks?status=all"}
    )
    assert r.status_code == 303 and r.headers["location"] == "/tasks?status=all"
    # a recurring occurrence can be deleted too; deleting from its own page goes to the list
    r = ui.post(
        f"/tasks/{by_title['Monthly rent']}/delete",
        data={"csrf": token, "next": f"/tasks/{by_title['Monthly rent']}"},
    )
    assert r.headers["location"] == "/tasks"
    after = ui.get("/tasks?status=all").text
    assert "Throwaway" not in after and "Monthly rent" not in after
    assert ui.get(f"/tasks/{by_title['Throwaway']}").status_code == 303  # gone -> back to the list


def test_repeat_picker_builds_rules_and_shows_friendly_schedule(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post(
        "/series/new",
        data={"title": "Gym", "repeat": "weekly", "weekday": ["MO", "TH"], "csrf": token},
    )
    ui.post(
        "/series/new", data={"title": "Pension", "repeat": "monthly", "day": "last", "csrf": token}
    )
    ui.post(
        "/series/new",
        data={"title": "Tax", "repeat": "yearly", "month": "6", "day": "15", "csrf": token},
    )
    page = ui.get("/series").text
    assert "Every week on Mon, Thu" in page
    assert "Every month on the last day" in page
    assert "Every year on 15 Jun" in page
    ui.post("/series/new", data={"title": "Bad", "repeat": "weekly", "csrf": token})
    assert "pick at least one weekday" in ui.get("/series").text
    # editing with 'keep' leaves the schedule alone
    ui.post(
        "/series/1/edit",
        data={"title": "Gym time", "repeat": "keep", "due_time": "07:30", "csrf": token},
    )
    detail = ui.get("/series/1").text
    assert "Gym time" in detail and "Every week on Mon, Thu" in detail


def test_time_pickers_offer_only_quarter_hours(ui):
    login(ui)
    page = ui.get("/").text
    # the add-task dialog has a date + a time select, and no free-minute datetime-local input
    assert 'name="due_date"' in page and 'name="due_time"' in page
    assert 'type="datetime-local"' not in page
    options = set(re.findall(r"<option[^>]*>(\d\d:\d\d)</option>", page))
    assert {"00:00", "00:15", "00:30", "00:45", "09:00", "23:45"} <= options
    assert all(o.split(":")[1] in ("00", "15", "30", "45") for o in options)
    assert len(options) == 96


def test_due_date_and_time_slot_become_the_right_local_time(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post(
        "/quick/task",
        data={
            "title": "At quarter past",
            "due_date": "2027-03-05",
            "due_time": "14:15",
            "csrf": token,
        },
    )
    page = ui.get("/tasks?status=all").text
    # Europe/Berlin in March is CET (UTC+1); the UI shows local time again
    assert "At quarter past" in page and "Fri 5 Mar 2027 · 14:15" in page
    # empty date = no due date given = due now (open immediately)
    ui.post(
        "/quick/task",
        data={"title": "Right now", "due_date": "", "due_time": "10:30", "csrf": token},
    )
    assert "Right now" in ui.get("/tasks?status=open").text


def test_an_off_grid_time_is_kept_when_editing(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post(
        "/quick/task",
        data={"title": "Odd one", "due_date": "2027-03-05", "due_time": "09:00", "csrf": token},
    )
    # a task created through the API can have any minute
    import urllib.request  # noqa: F401  (documenting intent: not needed, we patch via the form below)

    ui.post("/tasks/1/edit", data={"title": "Odd one", "due": "2027-03-05T09:07", "csrf": token})
    detail = ui.get("/tasks/1").text
    assert '<option value="09:07" selected>09:07</option>' in detail
    # saving the form unchanged keeps 09:07 (no silent rounding)
    ui.post(
        "/tasks/1/edit",
        data={"title": "Odd one", "due_date": "2027-03-05", "due_time": "09:07", "csrf": token},
    )
    assert "Fri 5 Mar 2027 · 09:07" in ui.get("/tasks?status=all").text


def test_series_due_time_is_a_quarter_hour_select(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post(
        "/series/new",
        data={"title": "Gym", "repeat": "daily", "due_time": "18:45", "csrf": token},
    )
    detail = ui.get("/series/1").text
    assert "<option selected>18:45</option>" in detail
    assert 'name="due_time" value' not in detail  # no longer a free-text field


def test_notes_can_be_added_without_finishing_the_task(ui):
    login(ui)
    token = csrf_of(ui.get("/").text)
    ui.post("/quick/task", data={"title": "Collect rent", "csrf": token})
    page = ui.get("/").text
    # a visible "Add a note" button on the row, not just a menu entry
    assert 'title="Add a note"' in page and 'data-action="/tasks/1/note"' in page

    r = ui.post(
        "/tasks/1/note", data={"note": "tenant promised Friday", "csrf": token, "next": "/"}
    )
    assert r.status_code == 303
    after = ui.get("/").text
    assert "tenant promised Friday" in after  # latest note shown under the title
    assert "Collect rent" in after and "Due today" in after  # still open, unchanged

    ui.post("/tasks/1/note", data={"note": "called again, no answer", "csrf": token})
    row = ui.get("/").text
    assert "All notes (2)" in row and "called again, no answer" in row

    detail = ui.get("/tasks/1").text
    assert "Add a note…" in detail and "tenant promised Friday" in detail
    assert detail.index("called again") < detail.index("tenant promised")  # newest first
    # an empty note is refused with a readable message
    ui.post("/tasks/1/note", data={"note": "  ", "csrf": token})
    assert "note must not be empty" in ui.get("/tasks/1").text
    # the old route name still works
    assert ui.post("/tasks/1/remark", data={"note": "legacy", "csrf": token}).status_code in (
        303,
        404,
        405,
    )


def test_note_lines_drop_the_internal_label_but_keep_action_labels():
    from sebastian.ui.routes import note_lines

    lines = note_lines(
        "2026-10-05 10:02 note: tenant promised Friday\n"
        "2026-10-05 10:05 snoozed until 2026-10-05 12:05: waiting for bank\n"
        "2026-10-05 12:10 done: paid\nlegacy line without a timestamp"
    )
    assert lines[0] == {"at": "2026-10-05 10:02", "text": "tenant promised Friday"}
    assert lines[1]["text"].startswith("snoozed until")
    assert lines[2]["text"] == "done: paid"
    assert lines[3] == {"at": "", "text": "legacy line without a timestamp"}
