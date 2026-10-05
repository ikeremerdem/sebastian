from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from freezegun import freeze_time


def test_health_is_open_but_api_requires_key(client):
    assert client.get("/health").json()["status"] == "ok"
    bare = TestClient(client.app)
    assert bare.get("/api/v1/tasks").status_code == 401
    assert bare.get("/api/v1/tasks", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/tasks").status_code == 200


def test_rent_story_end_to_end(client):
    with freeze_time("2026-03-01 08:00:00"):
        r = client.post(
            "/api/v1/series",
            json={
                "title": "Collect rent from tenant",
                "rrule": "FREQ=MONTHLY;BYMONTHDAY=5",
                "note": "Flat 3B, 950 EUR",
                "nag_interval_min": 60,
                "quiet_hours": "none",
            },
        )
        assert r.status_code == 201
        waiting = client.get("/api/v1/tasks", params={"status": "waiting"}).json()
        assert len(waiting) == 1 and waiting[0]["occurrence"] == "2026-03-05"
        assert waiting[0]["series_note"] == "Flat 3B, 950 EUR"

    with freeze_time("2026-03-05 08:30:00"):
        due = client.get("/api/v1/due").json()
        assert due["count"] == 1 and due["items"][0]["reason"] == "due"
        tid = due["items"][0]["id"]
        client.post(f"/api/v1/tasks/{tid}/notified")
        assert client.get("/api/v1/due").json()["count"] == 0

    with freeze_time("2026-03-05 09:45:00"):
        assert client.get("/api/v1/due").json()["items"][0]["reason"] == "nag"
        s = client.post(
            f"/api/v1/tasks/{tid}/snooze", json={"minutes": 90, "note": "tenant said tomorrow"}
        )
        assert s.json()["state"] == "snoozed"
        digest = client.get("/api/v1/digest/today").json()
        assert [t["id"] for t in digest["snoozed"]] == [tid]

    with freeze_time("2026-03-06 07:00:00"):
        done = client.post(f"/api/v1/tasks/{tid}/done", json={"note": "paid by transfer"}).json()
        assert done["status"] == "done"
        assert "tenant said tomorrow" in done["remarks"]
        history = client.get("/api/v1/tasks", params={"status": "done"}).json()
        assert [h["occurrence"] for h in history] == ["2026-03-05"]
        nxt = client.get("/api/v1/tasks", params={"status": "waiting"}).json()
        assert nxt[0]["occurrence"] == "2026-04-05"


def test_one_off_task_and_validation_errors(client):
    due = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = client.post(
        "/api/v1/tasks", json={"title": "Call dentist", "due_at": due, "note": "ask price"}
    )
    assert r.status_code == 201 and r.json()["recurring"] is False
    assert "ask price" in r.json()["remarks"]
    assert client.post("/api/v1/tasks", json={"title": "  "}).status_code == 422
    assert (
        client.post(
            "/api/v1/tasks", json={"title": "x", "due_at": "2026-03-05T09:00:00"}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/tasks/9999").status_code == 404
    bad = client.post("/api/v1/series", json={"title": "x", "rrule": "FREQ=NOPE"})
    assert bad.status_code == 422 and "invalid rrule" in bad.json()["detail"]
    tid = r.json()["id"]
    assert client.post(f"/api/v1/tasks/{tid}/done").status_code == 200
    assert client.post(f"/api/v1/tasks/{tid}/done").status_code == 409


def test_categories_and_entries_flow(client):
    cats = client.get("/api/v1/categories").json()
    assert [c["name"] for c in cats] == ["Note"]
    pt = client.post(
        "/api/v1/categories",
        json={"name": "Public Transport", "description": "count each trip"},
    )
    assert pt.status_code == 201
    assert client.post("/api/v1/categories", json={"name": "public transport"}).status_code == 409

    ok = client.post("/api/v1/entries", json={"category": "public transport"})
    assert ok.status_code == 201 and ok.json()["category"] == "Public Transport"
    bad = client.post("/api/v1/entries", json={"category": "transit"})
    assert bad.status_code == 422
    assert [c["name"] for c in bad.json()["valid_categories"]] == ["Note", "Public Transport"]
    assert client.post("/api/v1/entries", json={"category": "Note"}).status_code == 422

    n = client.post("/api/v1/entries", json={"category": "Note", "text": "Pizza Joint 18 EUR"})
    assert n.status_code == 201
    found = client.get("/api/v1/entries", params={"q": "pizza"}).json()
    assert [e["text"] for e in found] == ["Pizza Joint 18 EUR"]

    summary = client.get("/api/v1/entries/summary", params={"category": "Public Transport"}).json()
    assert summary["categories"][0]["total_entries"] == 1
    assert client.get("/api/v1/entries/summary", params={"group_by": "day"}).status_code == 422
    assert client.get("/api/v1/digest/entries", params={"period": "week"}).status_code == 200
    assert client.delete(f"/api/v1/entries/{ok.json()['id']}").status_code == 204


def test_settings_roundtrip(client):
    assert client.get("/api/v1/settings").json()["timezone"] == "Europe/Berlin"
    r = client.patch("/api/v1/settings", json={"quiet_hours": "23:00-06:00"})
    assert r.json()["quiet_hours"] == "23:00-06:00"
    assert client.patch("/api/v1/settings", json={"timezone": "Nowhere/Land"}).status_code == 422
