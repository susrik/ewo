"""HTTP /api endpoints via the FastAPI test client."""

from __future__ import annotations

from fastapi.testclient import TestClient


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

    patched = client.patch(f"/api/people/{person_id}", json={"email": "a@x.com"})
    assert patched.json()["email"] == "a@x.com"

    assert client.delete(f"/api/people/{person_id}").status_code == 200
    assert client.get(f"/api/people/{person_id}").status_code == 404


def test_people_404s(client: TestClient) -> None:
    assert client.patch("/api/people/999", json={"email": "x"}).status_code == 404
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


def test_reports_endpoints(client: TestClient) -> None:
    client.post("/api/jobs/what_next/run")
    reports = client.get("/api/reports").json()
    assert reports[0]["report_type"] == "what-next"

    detail = client.get(f"/api/reports/{reports[0]['id']}").json()
    assert "# What next" in detail["body"]

    assert client.get("/api/reports/999").status_code == 404
