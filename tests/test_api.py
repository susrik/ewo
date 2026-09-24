"""HTTP /api endpoints via the FastAPI test client."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ewo.config import NotesConfig
from ewo.core import note_items
from ewo.db.models import NoteItemKind


def _create_person(client: TestClient, name: str = "Anna") -> int:
    response = client.post("/api/people", json={"name": name})
    assert response.status_code == 201
    return int(response.json()["id"])


def _create_task(client: TestClient, title: str = "a task", **extra: object) -> int:
    response = client.post("/api/tasks", json={"title": title, **extra})
    assert response.status_code == 201
    return int(response.json()["id"])


# --- people ---


def test_people_crud(client: TestClient) -> None:
    person_id = _create_person(client)

    assert client.get("/api/people").json()[0]["name"] == "Anna"
    assert client.get(f"/api/people/{person_id}").status_code == 200

    patched = client.patch(
        f"/api/people/{person_id}", json={"notes_dir": "swd/people/anna", "aliases": ["Ann"]}
    )
    assert patched.json()["notes_dir"] == "swd/people/anna"
    assert patched.json()["aliases"] == ["Ann"]
    assert "email" not in patched.json()

    assert client.delete(f"/api/people/{person_id}").status_code == 200
    assert client.get(f"/api/people/{person_id}").status_code == 404


def test_people_404s(client: TestClient) -> None:
    assert client.patch("/api/people/999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/people/999").status_code == 404


# --- tasks ---


def test_task_crud(client: TestClient) -> None:
    person_id = _create_person(client)
    task_id = _create_task(
        client,
        title="ship it",
        priority="high",
        assignee_id=person_id,
        tags=["infra"],
        due_date="2026-09-15",
    )

    fetched = client.get(f"/api/tasks/{task_id}").json()
    assert fetched["priority"] == "high"
    assert fetched["assignee"]["name"] == "Anna"
    assert fetched["tags"][0]["name"] == "infra"

    patched = client.patch(f"/api/tasks/{task_id}", json={"status": "done", "tags": ["x"]})
    assert patched.json()["status"] == "done"
    assert patched.json()["tags"][0]["name"] == "x"

    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_task_filters(client: TestClient) -> None:
    person_id = _create_person(client)
    open_id = _create_task(client, title="open", assignee_id=person_id, tags=["t"])
    done_id = _create_task(client, title="done")
    client.patch(f"/api/tasks/{done_id}", json={"status": "done"})

    assert [t["id"] for t in client.get("/api/tasks").json()] == [open_id]
    assert len(client.get("/api/tasks", params={"include_closed": True}).json()) == 2
    assert [t["id"] for t in client.get("/api/tasks", params={"status": "done"}).json()] == [
        done_id
    ]
    assert [
        t["id"] for t in client.get("/api/tasks", params={"assignee_id": person_id}).json()
    ] == [open_id]
    assert [t["id"] for t in client.get("/api/tasks", params={"tag": "t"}).json()] == [open_id]


def test_task_404s(client: TestClient) -> None:
    assert client.patch("/api/tasks/999", json={"status": "done"}).status_code == 404
    assert client.delete("/api/tasks/999").status_code == 404


def test_notes(client: TestClient) -> None:
    task_id = _create_task(client)
    response = client.post("/api/notes", json={"body": "note body", "task_id": task_id})
    assert response.status_code == 201
    assert response.json()["body"] == "note body"


# --- one-on-ones ---


def test_one_on_one_flow(client: TestClient) -> None:
    person_id = _create_person(client)
    _create_task(client, title="anna work", assignee_id=person_id)

    meeting = client.post(
        "/api/one-on-ones", json={"person_id": person_id, "scheduled_for": "2026-09-02"}
    )
    assert meeting.status_code == 201
    meeting_id = meeting.json()["id"]

    item = client.post(f"/api/one-on-ones/{meeting_id}/agenda", json={"topic": "growth"})
    assert item.status_code == 201
    item_id = item.json()["id"]

    listed = client.get("/api/one-on-ones", params={"person_id": person_id}).json()
    assert len(listed) == 1
    assert listed[0]["agenda_items"][0]["topic"] == "growth"

    agenda = client.get(f"/api/one-on-ones/{meeting_id}/agenda.md").json()["markdown"]
    assert "growth" in agenda and "anna work" in agenda

    updated = client.patch(f"/api/agenda-items/{item_id}", json={"status": "discussed"})
    assert updated.json()["status"] == "discussed"


def test_one_on_one_404s(client: TestClient) -> None:
    assert (
        client.post(
            "/api/one-on-ones", json={"person_id": 999, "scheduled_for": "2026-09-02"}
        ).status_code
        == 404
    )
    assert client.post("/api/one-on-ones/999/agenda", json={"topic": "x"}).status_code == 404
    assert client.patch("/api/agenda-items/999", json={"status": "dropped"}).status_code == 404
    assert client.get("/api/one-on-ones/999/agenda.md").status_code == 404


# --- whatnext / reports / jobs ---


def test_whatnext(client: TestClient) -> None:
    _create_task(client, title="urgent thing", priority="critical")
    markdown = client.get("/api/whatnext").json()["markdown"]
    assert "urgent thing" in markdown
    assert "## Advice" not in markdown

    with_llm = client.get("/api/whatnext", params={"use_llm": True}).json()["markdown"]
    assert "## Advice" in with_llm


def test_jobs_endpoints(client: TestClient) -> None:
    jobs = client.get("/api/jobs").json()["jobs"]
    assert "daily_report" in jobs

    run = client.post("/api/jobs/what_next/run")
    assert run.status_code == 200
    assert run.json()["status"] == "success"

    assert client.post("/api/jobs/nope/run").status_code == 404

    runs = client.get("/api/jobs/runs").json()
    assert runs[0]["job_name"] == "what_next"
    assert runs[0]["result"] == "ok"

    scan = client.post("/api/jobs/notes_scan/run", params={"full": True}).json()
    assert scan["status"] == "success" and scan["result"] == "notes disabled"


# --- people from notes / inbox ---


def test_seed_people_requires_notes(client: TestClient) -> None:
    assert client.post("/api/people/seed-from-notes").status_code == 400


def test_seed_people_from_notes(client: TestClient, notes_root: Path) -> None:
    client.app.state.config.notes = NotesConfig(enabled=True, root=notes_root)  # type: ignore[attr-defined]
    created = client.post("/api/people/seed-from-notes").json()["created"]
    assert [p["name"] for p in created] == ["James L", "James S"]
    assert created[0]["notes_dir"] == "swd/people/james_l"
    assert client.post("/api/people/seed-from-notes").json()["created"] == []


def test_note_items_endpoints(client: TestClient, session: Session) -> None:
    owner_id = _create_person(client, "Neda")
    item, _ = note_items.upsert_item(
        session, "swd/x.md", 7, "Add SSO", NoteItemKind.ACTION, owner_id=owner_id
    )
    other, _ = note_items.upsert_item(session, "ai/y.md", 2, "Budget", NoteItemKind.RISK)
    third, _ = note_items.upsert_item(session, "ai/z.md", 9, "Old thing", NoteItemKind.ACTION)
    session.commit()

    listed = client.get("/api/note-items").json()
    assert [i["summary"] for i in listed] == ["Budget", "Old thing", "Add SSO"]
    assert listed[2]["owner"]["name"] == "Neda"
    assert client.get("/api/note-items", params={"owner_id": owner_id}).json()[0]["id"] == item.id
    assert len(client.get("/api/note-items", params={"path_prefix": "ai/"}).json()) == 2

    patched = client.patch(f"/api/note-items/{item.id}", json={"summary": "Add SSO to glitchtip"})
    assert patched.json()["summary"] == "Add SSO to glitchtip"

    accepted = client.post(
        f"/api/note-items/{item.id}/accept", json={"priority": "high", "due_date": "2026-10-01"}
    )
    assert accepted.status_code == 201
    task = accepted.json()
    assert task["source"] == "notes" and task["assignee"]["name"] == "Neda"
    assert task["external_links"][0] == {
        **task["external_links"][0],
        "system": "notes",
        "external_key": f"swd/x.md:7:{item.id}",
    }

    assert client.post(f"/api/note-items/{other.id}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/note-items/{third.id}/done").json()["status"] == "already_done"
    assert client.get("/api/note-items").json() == []
    assert len(client.get("/api/note-items", params={"status": ""}).json()) == 3
    assert (
        client.get("/api/note-items", params={"status": "accepted"}).json()[0]["task_id"]
        == task["id"]
    )


def test_note_items_404s(client: TestClient) -> None:
    assert client.patch("/api/note-items/999", json={"summary": "x"}).status_code == 404
    assert client.post("/api/note-items/999/accept", json={}).status_code == 404
    assert client.post("/api/note-items/999/dismiss").status_code == 404
    assert client.post("/api/note-items/999/done").status_code == 404


def test_reports_endpoints(client: TestClient) -> None:
    client.post("/api/jobs/what_next/run")
    reports = client.get("/api/reports").json()
    assert reports[0]["report_type"] == "what-next"

    detail = client.get(f"/api/reports/{reports[0]['id']}").json()
    assert "# What next" in detail["body"]

    assert client.get("/api/reports/999").status_code == 404
