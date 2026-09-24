"""GUI pages and htmx fragments."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ewo.config import NotesConfig
from ewo.core import note_items
from ewo.db.models import NoteItemKind


def _item(session: Session, summary: str = "chase Matti", path: str = "eurohpc/x.md") -> int:
    item, _ = note_items.upsert_item(session, path, 34, summary, NoteItemKind.ACTION, "- chase")
    session.commit()
    return item.id


# --- dashboard ---


def test_index_page(client: TestClient) -> None:
    client.post("/api/tasks", json={"title": "visible task", "priority": "critical"})
    client.post(
        "/api/tasks",
        json={"title": "late", "due_date": (date.today() - timedelta(days=1)).isoformat()},
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "visible task" in response.text
    assert "late" in response.text  # deadlines list
    assert "Notes scanning is disabled" in response.text
    assert "No person is marked as you" in response.text


def test_index_quick_add(client: TestClient) -> None:
    response = client.post(
        "/gui/tasks", data={"title": "quick", "compact": "1", "tags": "a, b", "due_date": ""}
    )
    assert "Added" in response.text and "quick" in response.text
    listing = client.get("/api/tasks").json()
    assert sorted(t["name"] for t in listing[0]["tags"]) == ["a", "b"]


# --- tasks ---


def test_tasks_page_and_filters(client: TestClient) -> None:
    person = client.post("/api/people", json={"name": "Anna"}).json()
    client.post("/api/tasks", json={"title": "for anna", "assignee_id": person["id"]})
    client.post("/api/tasks", json={"title": "floating", "tags": ["x"]})

    page = client.get("/tasks")
    assert page.status_code == 200
    assert "for anna" in page.text and "floating" in page.text

    filtered = client.get("/gui/tasks", params={"assignee": str(person["id"])})
    assert "for anna" in filtered.text and "floating" not in filtered.text

    by_tag = client.get("/gui/tasks", params={"tag": "x", "source": "manual"})
    assert "floating" in by_tag.text and "for anna" not in by_tag.text

    by_status = client.get("/gui/tasks", params={"status": "blocked"})
    assert "No tasks match" in by_status.text


def test_task_fragment_and_create(client: TestClient) -> None:
    response = client.post(
        "/gui/tasks", data={"title": "from form", "priority": "high", "assignee_id": ""}
    )
    assert response.status_code == 200
    assert "from form" in response.text
    assert "from form" in client.get("/gui/tasks").text


def test_task_create_with_assignee(client: TestClient) -> None:
    person = client.post("/api/people", json={"name": "Anna"}).json()
    response = client.post(
        "/gui/tasks",
        data={"title": "assigned", "priority": "normal", "assignee_id": str(person["id"])},
    )
    assert "Anna" in response.text


def test_task_status_change_rerenders_row(client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "to close"}).json()
    response = client.post(f"/gui/tasks/{task['id']}/status", data={"status": "done"})
    assert response.status_code == 200
    assert "to close" in response.text and "opacity-50" in response.text
    assert "to close" not in client.get("/gui/tasks").text


def test_task_edit_roundtrip(client: TestClient) -> None:
    person = client.post("/api/people", json={"name": "Anna"}).json()
    task = client.post("/api/tasks", json={"title": "old title"}).json()

    edit_form = client.get(f"/gui/tasks/{task['id']}/edit")
    assert 'value="old title"' in edit_form.text

    saved = client.post(
        f"/gui/tasks/{task['id']}",
        data={
            "title": "new title",
            "priority": "high",
            "status": "in_progress",
            "assignee_id": str(person["id"]),
            "due_date": "2026-10-01",
            "tags": "x, y",
            "description": "details",
        },
    )
    assert "new title" in saved.text and "Anna" in saved.text and "2026-10-01" in saved.text
    fresh = client.get(f"/api/tasks/{task['id']}").json()
    assert fresh["description"] == "details"
    assert fresh["status"] == "in_progress"

    row = client.get(f"/gui/tasks/{task['id']}")
    assert "new title" in row.text


def test_task_detail_and_notes(client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "with notes"}).json()
    assert "source: manual" in client.get(f"/gui/tasks/{task['id']}/detail").text
    response = client.post(f"/gui/tasks/{task['id']}/notes", data={"body": "remember this"})
    assert "remember this" in response.text
    blank = client.post(f"/gui/tasks/{task['id']}/notes", data={"body": "   "})
    assert blank.text.count("remember this") == 1


# --- inbox ---


def test_inbox_page_and_actions(client: TestClient, session: Session) -> None:
    item_id = _item(session)
    other = _item(session, "budget", "ai/y.md")

    page = client.get("/inbox")
    assert page.status_code == 200
    assert "chase Matti" in page.text and "eurohpc/x.md" in page.text
    assert "notes disabled in config" in page.text
    assert "Never scanned" in page.text

    accepted = client.post(
        f"/gui/inbox/{item_id}/accept",
        data={"priority": "high", "assignee_id": "", "due_date": "2026-10-01"},
    )
    assert "accepted" in accepted.text and "task #" in accepted.text
    task = client.get("/api/tasks").json()[0]
    assert task["title"] == "chase Matti" and task["priority"] == "high"
    assert task["external_links"][0]["external_key"] == f"eurohpc/x.md:34:{item_id}"

    done = client.post(f"/gui/inbox/{other}/done")
    assert "already done" in done.text

    assert "Nothing to review" in client.get("/gui/inbox").text
    assert "budget" in client.get("/gui/inbox", params={"status": "all"}).text
    assert "budget" in client.get("/gui/inbox", params={"status": "already_done"}).text


def test_inbox_dismiss_and_owner_filter(client: TestClient, session: Session) -> None:
    person = client.post("/api/people", json={"name": "Neda"}).json()
    item, _ = note_items.upsert_item(
        session, "swd/x.md", 3, "fix pipeline", NoteItemKind.ACTION, owner_id=person["id"]
    )
    session.commit()
    _item(session, "unowned", "misc.md")

    mine = client.get("/gui/inbox", params={"owner": str(person["id"])})
    assert "fix pipeline" in mine.text and "unowned" not in mine.text

    dismissed = client.post(f"/gui/inbox/{item.id}/dismiss")
    assert "dismissed" in dismissed.text


def test_inbox_scan_button_when_disabled(client: TestClient) -> None:
    response = client.post("/gui/scan", data={"full": "false"})
    assert response.status_code == 200
    assert "success" in response.text and "notes disabled" in response.text
    assert "notes disabled" in client.get("/inbox").text  # last_scan shown


# --- people ---


def test_people_page_and_fragment(client: TestClient) -> None:
    page = client.get("/people")
    assert page.status_code == 200 and "No people yet" in page.text

    response = client.post(
        "/gui/people",
        data={"name": "Bob", "notes_dir": "swd/people/bob", "aliases": "Bobby, Rob", "is_self": ""},
    )
    assert (
        "Bob" in response.text
        and "swd/people/bob" in response.text
        and "Bobby, Rob" in response.text
    )
    assert "No people yet" not in client.get("/gui/people").text

    bob = client.get("/api/people").json()[0]
    marked = client.post(f"/gui/people/{bob['id']}/self")
    assert ">me<" in marked.text
    assert client.get(f"/api/people/{bob['id']}").json()["is_self"] is True


def test_people_seed_noop_when_notes_disabled(client: TestClient) -> None:
    response = client.post("/gui/people/seed")
    assert response.status_code == 200 and "No people yet" in response.text


def test_people_seed_from_notes(client: TestClient, notes_root: Path) -> None:
    client.app.state.config.notes = NotesConfig(enabled=True, root=notes_root)  # type: ignore[attr-defined]
    assert "Seed from notes" in client.get("/people").text
    response = client.post("/gui/people/seed")
    assert "James L" in response.text and "swd/people/james_s" in response.text


def test_one_on_ones_fragment(client: TestClient) -> None:
    empty = client.get("/gui/one-on-ones")
    assert "No 1:1 meetings yet" in empty.text

    person = client.post("/api/people", json={"name": "Anna"}).json()
    meeting = client.post(
        "/api/one-on-ones", json={"person_id": person["id"], "scheduled_for": "2026-09-02"}
    ).json()
    client.post(f"/api/one-on-ones/{meeting['id']}/agenda", json={"topic": "topic-x"})

    response = client.get("/gui/one-on-ones")
    assert "Anna" in response.text and "topic-x" in response.text
    assert "2026-09-02" in client.get("/people").text  # last 1:1 column


# --- jobs ---


def test_jobs_page_and_run(client: TestClient) -> None:
    page = client.get("/jobs")
    assert page.status_code == 200
    assert "notes_scan" in page.text and "what_next" in page.text
    assert "No job runs yet" in client.get("/gui/job-runs").text

    response = client.post("/gui/jobs/what_next/run")
    assert "what_next" in response.text and "success" in response.text
