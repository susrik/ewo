"""GUI pages and htmx fragments."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_index_page(client: TestClient) -> None:
    client.post("/api/tasks", json={"title": "visible task"})
    response = client.get("/")
    assert response.status_code == 200
    assert "ewo" in response.text
    assert "visible task" in response.text


def test_task_fragment_and_create(client: TestClient) -> None:
    response = client.post(
        "/gui/tasks", data={"title": "from form", "priority": "high", "assignee_id": ""}
    )
    assert response.status_code == 200
    assert "from form" in response.text

    listing = client.get("/gui/tasks")
    assert "from form" in listing.text


def test_task_create_with_assignee(client: TestClient) -> None:
    person = client.post("/api/people", json={"name": "Anna"}).json()
    response = client.post(
        "/gui/tasks",
        data={"title": "assigned", "priority": "normal", "assignee_id": str(person["id"])},
    )
    assert "Anna" in response.text


def test_task_status_change(client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "to close"}).json()
    response = client.post(f"/gui/tasks/{task['id']}/status", data={"status": "done"})
    assert response.status_code == 200
    assert "to close" not in response.text  # done tasks hidden from open list


def test_people_fragment(client: TestClient) -> None:
    response = client.post("/gui/people", data={"name": "Bob", "email": ""})
    assert "Bob" in response.text
    assert "No people yet" not in client.get("/gui/people").text


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


def test_job_runs_fragment(client: TestClient) -> None:
    assert "No job runs yet" in client.get("/gui/job-runs").text
    client.post("/api/jobs/what_next/run")
    response = client.get("/gui/job-runs")
    assert "what_next" in response.text and "success" in response.text
