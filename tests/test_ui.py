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
