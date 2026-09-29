"""HTTP /api endpoints via the FastAPI test client."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ewo.config import NotesConfig
from ewo.core import nuggets
from ewo.db.models import NuggetKind


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
    assert fetched["parent_id"] is None and fetched["completed_at"] is None

    patched = client.patch(f"/api/tasks/{task_id}", json={"status": "done", "tags": ["x"]})
    assert patched.json()["status"] == "done"
    assert patched.json()["tags"][0]["name"] == "x"
    assert patched.json()["completed_at"] is not None

    reopened = client.patch(f"/api/tasks/{task_id}", json={"status": "open"})
    assert reopened.json()["completed_at"] is None

    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_task_hierarchy(client: TestClient) -> None:
    parent_id = _create_task(client, title="epic")
    child_id = _create_task(client, title="story", parent_id=parent_id)
    fetched = client.get(f"/api/tasks/{child_id}").json()
    assert fetched["parent_id"] == parent_id

    assert client.post("/api/tasks", json={"title": "bad", "parent_id": 999}).status_code == 404
    assert client.patch(f"/api/tasks/{parent_id}", json={"parent_id": child_id}).status_code == 400
    assert client.patch(f"/api/tasks/{child_id}", json={"parent_id": child_id}).status_code == 400

    # reparenting works when it creates no cycle
    other_id = _create_task(client, title="other epic")
    assert (
        client.patch(f"/api/tasks/{child_id}", json={"parent_id": other_id}).json()["parent_id"]
        == other_id
    )


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


def test_task_multi_tag_filter(client: TestClient) -> None:
    only_x = _create_task(client, title="only x", tags=["x"])
    only_y = _create_task(client, title="only y", tags=["y"])
    both = _create_task(client, title="both", tags=["x", "y"])

    any_ids = {
        t["id"] for t in client.get("/api/tasks", params=[("tags", "x"), ("tags", "y")]).json()
    }
    assert any_ids == {only_x, only_y, both}

    all_response = client.get(
        "/api/tasks", params=[("tags", "x"), ("tags", "y"), ("tag_match", "all")]
    )
    assert [t["id"] for t in all_response.json()] == [both]

    # the legacy single-tag param merges into the list
    merged = client.get(
        "/api/tasks", params=[("tag", "x"), ("tags", "y"), ("tag_match", "all")]
    ).json()
    assert [t["id"] for t in merged] == [both]

    assert client.get("/api/tasks", params={"tags": ["nope"]}).json() == []
    assert client.get("/api/tasks", params={"tag_match": "bogus"}).status_code == 422


def test_tag_endpoints(client: TestClient) -> None:
    task_id = _create_task(client, title="tagged", tags=["one", "two"])

    listed = client.get("/api/tags").json()
    assert [t["name"] for t in listed] == ["one", "two"]
    assert all(t["description"] is None for t in listed)

    tag_id = next(t["id"] for t in listed if t["name"] == "one")
    patched = client.patch(f"/api/tags/{tag_id}", json={"description": "the first"})
    assert patched.json() == {"id": tag_id, "name": "one", "description": "the first"}

    renamed = client.patch(f"/api/tags/{tag_id}", json={"name": " Uno "})
    assert renamed.json()["name"] == "uno"

    # collision with another tag's normalized name -> 400
    assert client.patch(f"/api/tags/{tag_id}", json={"name": "TWO"}).status_code == 400
    # missing tag -> 404
    assert client.patch("/api/tags/999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/tags/999").status_code == 404

    assert client.delete(f"/api/tags/{tag_id}").json() == {"detail": "deleted"}
    remaining = client.get(f"/api/tasks/{task_id}").json()["tags"]
    assert [t["name"] for t in remaining] == ["two"]


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


def test_nugget_endpoints(client: TestClient, session: Session) -> None:
    owner_id = _create_person(client, "Neda")
    item, _ = nuggets.upsert_nugget(
        session, "swd/x.md", 7, "Add SSO", NuggetKind.ACTION, owner_id=owner_id
    )
    other, _ = nuggets.upsert_nugget(session, "ai/y.md", 2, "Budget", NuggetKind.RISK)
    third, _ = nuggets.upsert_nugget(session, "ai/z.md", 9, "Old thing", NuggetKind.ACTION)
    session.commit()

    listed = client.get("/api/nuggets").json()
    assert [i["summary"] for i in listed] == ["Budget", "Old thing", "Add SSO"]
    assert listed[2]["owner"]["name"] == "Neda"
    assert listed[2]["jira_keys"] == [] and listed[2]["suggested_task_id"] is None
    assert client.get("/api/nuggets", params={"owner_id": owner_id}).json()[0]["id"] == item.id
    assert len(client.get("/api/nuggets", params={"path_prefix": "ai/"}).json()) == 2

    patched = client.patch(f"/api/nuggets/{item.id}", json={"summary": "Add SSO to glitchtip"})
    assert patched.json()["summary"] == "Add SSO to glitchtip"

    attached = client.post(
        f"/api/nuggets/{item.id}/attach", json={"priority": "high", "due_date": "2026-10-01"}
    )
    assert attached.status_code == 201
    task = attached.json()
    assert task["source"] == "notes" and task["assignee"]["name"] == "Neda"
    assert task["external_links"][0] == {
        **task["external_links"][0],
        "system": "notes",
        "external_key": f"swd/x.md:7:{item.id}",
    }

    assert client.post(f"/api/nuggets/{other.id}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/nuggets/{third.id}/done").json()["status"] == "already_done"
    assert client.get("/api/nuggets").json() == []
    assert len(client.get("/api/nuggets", params={"status": ""}).json()) == 3
    assert (
        client.get("/api/nuggets", params={"status": "attached"}).json()[0]["task_id"] == task["id"]
    )


def test_nugget_attach_move_detach(client: TestClient, session: Session) -> None:
    first = client.post("/api/tasks", json={"title": "first"}).json()
    second = client.post("/api/tasks", json={"title": "second"}).json()
    item, _ = nuggets.upsert_nugget(
        session, "swd/x.md", 3, "detail", NuggetKind.ACTION, jira_keys=["PROJ-9"]
    )
    session.commit()

    # attach to an existing task
    attached = client.post(f"/api/nuggets/{item.id}/attach", json={"task_id": first["id"]})
    assert attached.status_code == 201
    assert attached.json()["title"] == "first"
    keys = {(link["system"], link["external_key"]) for link in attached.json()["external_links"]}
    assert ("jira", "PROJ-9") in keys  # jira keys follow the nugget

    # task nuggets listing
    listed = client.get(f"/api/tasks/{first['id']}/nuggets").json()
    assert [n["id"] for n in listed] == [item.id]
    assert client.get("/api/tasks/999/nuggets").status_code == 404

    # move to another task
    moved = client.post(f"/api/nuggets/{item.id}/move", json={"task_id": second["id"]})
    assert moved.json()["task_id"] == second["id"]
    assert client.get(f"/api/tasks/{second['id']}/nuggets").json()[0]["id"] == item.id
    assert client.post(f"/api/nuggets/{item.id}/move", json={"task_id": 999}).status_code == 404

    # detach returns it to the inbox
    detached = client.post(f"/api/nuggets/{item.id}/detach")
    assert detached.json()["status"] == "new" and detached.json()["task_id"] is None
    assert client.get(f"/api/tasks/{second['id']}/nuggets").json() == []


def test_nugget_404s(client: TestClient) -> None:
    assert client.patch("/api/nuggets/999", json={"summary": "x"}).status_code == 404
    assert client.post("/api/nuggets/999/attach", json={}).status_code == 404
    assert client.post("/api/nuggets/999/dismiss").status_code == 404
    assert client.post("/api/nuggets/999/done").status_code == 404
    assert client.post("/api/nuggets/999/detach").status_code == 404


def test_task_link_endpoints(client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "linkable"}).json()
    created = client.post(
        f"/api/tasks/{task['id']}/links", json={"system": "jira", "external_key": "PROJ-1"}
    )
    assert created.status_code == 201
    missing = client.post("/api/tasks/999/links", json={"system": "jira", "external_key": "P-1"})
    assert missing.status_code == 404

    # the same jira issue can sit on a second task
    other = client.post("/api/tasks", json={"title": "also linkable"}).json()
    again = client.post(
        f"/api/tasks/{other['id']}/links", json={"system": "jira", "external_key": "PROJ-1"}
    )
    assert again.status_code == 201

    [link] = [link for link in created.json()["external_links"] if link["system"] == "jira"]
    assert client.delete(f"/api/tasks/{task['id']}/links/{link['id']}").status_code == 200
    assert client.get(f"/api/tasks/{task['id']}").json()["external_links"] == []
    assert client.delete(f"/api/tasks/{task['id']}/links/{link['id']}").status_code == 404
    assert client.delete(f"/api/tasks/{task['id']}/links/999").status_code == 404


def test_reports_endpoints(client: TestClient) -> None:
    client.post("/api/jobs/what_next/run")
    reports = client.get("/api/reports").json()
    assert reports[0]["report_type"] == "what-next"

    detail = client.get(f"/api/reports/{reports[0]['id']}").json()
    assert "# What next" in detail["body"]

    assert client.get("/api/reports/999").status_code == 404


def test_run_job_no_wait(client: TestClient) -> None:
    import time

    response = client.post("/api/jobs/what_next/run", params={"wait": "false"})
    assert response.status_code == 202
    run_id = response.json()["id"]

    deadline = time.time() + 5
    status = ""
    while time.time() < deadline:
        [run] = [r for r in client.get("/api/jobs/runs").json() if r["id"] == run_id]
        status = run["status"]
        if status != "running":
            break
        time.sleep(0.05)
    assert status == "success"

    assert client.post("/api/jobs/nope/run", params={"wait": "false"}).status_code == 404
