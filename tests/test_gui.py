"""GUI pages and htmx fragments."""

from __future__ import annotations

import json
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ewo.config import NotesConfig
from ewo.core import nuggets
from ewo.db.models import NuggetKind


def _item(session: Session, summary: str = "chase Matti", path: str = "eurohpc/x.md") -> int:
    item, _ = nuggets.upsert_nugget(session, path, 34, summary, NuggetKind.ACTION, "- chase")
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


def test_tasks_filter_saved_and_restored(client: TestClient) -> None:
    closed = client.post("/api/tasks", json={"title": "closed"}).json()
    client.patch(f"/api/tasks/{closed['id']}", json={"status": "done"})
    client.post("/api/tasks", json={"title": "open one"})

    # changing a filter persists it
    client.get("/gui/tasks", params={"status": "done"})

    # a fresh page load (and list fragment) with no params restores the saved filter
    page = client.get("/tasks")
    assert "closed" in page.text and "open one" not in page.text
    tree = client.get("/gui/tasks")
    assert "closed" in tree.text and "open one" not in tree.text

    # an explicit filter still overrides the saved one
    all_tasks = client.get("/gui/tasks", params={"status": "open"})
    assert "open one" in all_tasks.text and "closed" not in all_tasks.text


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


def test_task_hierarchy_display_and_edit(client: TestClient) -> None:
    parent = client.post("/api/tasks", json={"title": "epic"}).json()
    client.post("/api/tasks", json={"title": "story", "parent_id": parent["id"]})

    page = client.get("/tasks")
    assert "epic" in page.text and "story" in page.text  # flat list: no tree indentation

    # the child's detail panel lists its parent (refocus link) and the parent's lists the child
    [child] = [t for t in client.get("/api/tasks").json() if t["title"] == "story"]
    child_detail = client.get(f"/gui/tasks/{child['id']}/detail")
    assert "Parent" in child_detail.text and "epic" in child_detail.text
    parent_detail = client.get(f"/gui/tasks/{parent['id']}/detail")
    assert "Sub-tasks" in parent_detail.text and "story" in parent_detail.text

    edit_form = client.get(f"/gui/tasks/{parent['id']}/edit")
    assert 'name="parent_id"' in edit_form.text
    # the parent select must not offer the task itself
    assert f'value="{parent["id"]}"' not in edit_form.text

    saved = client.post(
        f"/gui/tasks/{parent['id']}",
        data={
            "title": "epic",
            "priority": "normal",
            "status": "open",
            "start_date": "2026-09-20",
        },
    )
    assert saved.status_code == 200
    assert client.get(f"/api/tasks/{parent['id']}").json()["start_date"] == "2026-09-20"

    # a closed current parent stays selectable in the child's edit form
    client.patch(f"/api/tasks/{parent['id']}", json={"status": "done"})
    [child] = [t for t in client.get("/api/tasks").json() if t["title"] == "story"]
    child_edit = client.get(f"/gui/tasks/{child['id']}/edit")
    assert f'value="{parent["id"]}"' in child_edit.text


def test_task_detail_children_and_links(client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "parent"}).json()

    response = client.post(f"/gui/tasks/{task['id']}/children", data={"title": "child one"})
    assert "child one" in response.text
    [child] = [t for t in client.get("/api/tasks").json() if t["title"] == "child one"]
    assert child["parent_id"] == task["id"]

    linked = client.post(f"/gui/tasks/{task['id']}/links", data={"jira_keys": "PROJ-1, PROJ-2"})
    assert "PROJ-1" in linked.text and "PROJ-2" in linked.text
    task_json = client.get(f"/api/tasks/{task['id']}").json()
    assert {link["external_key"] for link in task_json["external_links"]} == {"PROJ-1", "PROJ-2"}

    [link] = [x for x in task_json["external_links"] if x["external_key"] == "PROJ-1"]
    client.post(f"/gui/tasks/{task['id']}/links/{link['id']}/delete")
    remaining = {
        x["external_key"] for x in client.get(f"/api/tasks/{task['id']}").json()["external_links"]
    }
    assert remaining == {"PROJ-2"}


def test_task_sort(client: TestClient) -> None:
    client.post("/api/tasks", json={"title": "high task", "priority": "high"})
    client.post("/api/tasks", json={"title": "critical task", "priority": "critical"})
    client.post("/api/tasks", json={"title": "another high", "priority": "high"})

    listing = client.get("/gui/tasks").text
    assert listing.index("critical task") < listing.index("high task")
    assert listing.index("another high") < listing.index("high task")

    by_title = client.get("/gui/tasks", params={"sort": "title"}).text
    assert (
        by_title.index("another high")
        < by_title.index("critical task")
        < by_title.index("high task")
    )

    reversed_ = client.get("/gui/tasks", params={"sort": "title", "reverse": "true"}).text
    assert reversed_.index("high task") < reversed_.index("critical task")


def test_task_detach_from_parent(client: TestClient) -> None:
    parent = client.post("/api/tasks", json={"title": "epic"}).json()
    child = client.post("/api/tasks", json={"title": "story", "parent_id": parent["id"]}).json()

    panel = client.get(f"/gui/tasks/{parent['id']}/detail")
    assert "Detach" in panel.text

    detached = client.post(f"/gui/tasks/{child['id']}/detach")
    assert "story" not in detached.text  # no longer listed as a sub-task
    assert client.get(f"/api/tasks/{child['id']}").json()["parent_id"] is None

    # detaching an already-root task re-renders its own (unchanged) detail
    again = client.post(f"/gui/tasks/{child['id']}/detach")
    assert again.status_code == 200 and "No parent task" in again.text


def test_task_detail_nugget_management(client: TestClient, session: Session) -> None:
    first = client.post("/api/tasks", json={"title": "first"}).json()
    second = client.post("/api/tasks", json={"title": "second"}).json()
    item_id = _item(session)
    client.post(f"/api/nuggets/{item_id}/attach", json={"task_id": first["id"]})

    detail = client.get(f"/gui/tasks/{first['id']}/detail")
    assert "Nuggets (from notes)" in detail.text and "chase Matti" in detail.text
    assert "eurohpc/x.md:34" in detail.text

    # edit the nugget text in place
    edited = client.post(
        f"/gui/nuggets/{item_id}/edit",
        data={"summary": "chase Matti harder", "kind": "risk", "due_date": "2026-11-01"},
    )
    assert "chase Matti harder" in edited.text
    nugget = client.get("/api/nuggets", params={"status": "attached"}).json()[0]
    assert nugget["summary"] == "chase Matti harder" and nugget["kind"] == "risk"
    assert nugget["due_date"] == "2026-11-01"

    # move to the second task (the panel re-renders the first task, now empty)
    moved = client.post(f"/gui/nuggets/{item_id}/move", data={"task_id": str(second["id"])})
    assert "chase Matti harder" not in moved.text
    assert client.get(f"/api/tasks/{second['id']}/nuggets").json()[0]["id"] == item_id

    # detach sends it back to the inbox
    detached = client.post(f"/gui/nuggets/{item_id}/detach")
    assert "chase Matti harder" not in detached.text
    assert client.get("/api/nuggets").json()[0]["id"] == item_id  # status=new again


def test_tasks_organize_flow(client: TestClient, session: Session) -> None:
    # no llm key configured → the button is hidden, and the endpoint explains
    assert "Organize (AI)" not in client.get("/tasks").text
    response = client.post("/gui/tasks/organize")
    assert "No LLM API key" in response.text

    # with a key, proposals come from the (fake) LLM; applying re-renders the list
    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    first = client.post("/api/tasks", json={"title": "first"}).json()
    second = client.post("/api/tasks", json={"title": "second"}).json()
    from ewo.core.llm import FakeLLM

    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[
            '{"proposals": [{"kind": "merge", '
            f'"into_id": {first["id"]}, "from_id": {second["id"]}, "reason": "same"'
            "}]}"
        ]
    )
    proposals = client.post("/gui/tasks/organize")
    assert f"Merge #{second['id']} into #{first['id']}" in proposals.text

    applied = client.post(
        "/gui/tasks/organize/merge",
        data={"into_id": str(first["id"]), "from_id": str(second["id"])},
    )
    assert applied.status_code == 200
    assert client.get(f"/api/tasks/{second['id']}").status_code == 404

    # an LLM returning garbage surfaces as an inline error, not a 500
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    errored = client.post("/gui/tasks/organize")
    assert "did not return valid proposals" in errored.text


def test_tasks_organize_split_create_retitle(client: TestClient, session: Session) -> None:
    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    task = client.post("/api/tasks", json={"title": "mixed"}).json()
    item_id = _item(session)
    client.post(f"/api/nuggets/{item_id}/attach", json={"task_id": task["id"]})
    loose = _item(session, "loose one", "ai/y.md")
    loose2 = _item(session, "loose two", "ai/z.md")

    split = client.post(
        "/gui/tasks/organize/split",
        data={"task_id": str(task["id"]), "title": "half", "nugget_ids": str(item_id)},
    )
    assert "half" in split.text
    [attached] = client.get("/api/nuggets", params={"status": "attached"}).json()
    assert attached["id"] == item_id and attached["task_id"] != task["id"]

    created = client.post(
        "/gui/tasks/organize/create",
        data={"title": "cluster", "nugget_ids": f"{loose},{loose2}"},
    )
    assert "cluster" in created.text
    assert client.get("/api/nuggets").json() == []  # both attached now

    retitled = client.post(
        "/gui/tasks/organize/retitle", data={"task_id": str(task["id"]), "title": "renamed"}
    )
    assert "renamed" in retitled.text


def test_tasks_organize_batch_apply(client: TestClient, session: Session) -> None:
    task = client.post("/api/tasks", json={"title": "mixed"}).json()

    proposals_json = json.dumps(
        [
            {"kind": "retitle", "task_id": task["id"], "title": "renamed"},
            {"kind": "merge", "into_id": task["id"], "from_id": task["id"]},
        ]
    )
    applied = client.post(
        "/gui/tasks/organize/apply",
        data={"proposals_json": proposals_json, "selected": ["0"]},
    )
    assert "renamed" in applied.text
    # only the checked proposal ran; the (unchecked) self-merge was skipped
    assert client.get(f"/api/tasks/{task['id']}").json()["title"] == "renamed"

    # selecting none is an inline error
    none = client.post("/gui/tasks/organize/apply", data={"proposals_json": "[]"})
    assert "no proposals selected" in none.text

    # a checked proposal that fails is reported inline
    bad = client.post(
        "/gui/tasks/organize/apply",
        data={
            "proposals_json": json.dumps(
                [{"kind": "merge", "into_id": task["id"], "from_id": task["id"]}]
            ),
            "selected": ["0"],
        },
    )
    assert "into itself" in bad.text


def test_task_merge_from_edit(client: TestClient, session: Session) -> None:
    into = client.post("/api/tasks", json={"title": "survivor"}).json()
    loser = client.post("/api/tasks", json={"title": "to merge"}).json()

    edit = client.get(f"/gui/tasks/{loser['id']}/edit")
    assert "Merge this task into" in edit.text
    assert f'value="{into["id"]}"' in edit.text

    merged = client.post(f"/gui/tasks/{loser['id']}/merge", data={"into_id": str(into["id"])})
    assert merged.status_code == 200
    assert client.get(f"/api/tasks/{loser['id']}").status_code == 404
    assert client.get(f"/api/tasks/{into['id']}").status_code == 200


def test_task_description_suggest_apply(client: TestClient, session: Session) -> None:
    task = client.post("/api/tasks", json={"title": "desc", "description": "old"}).json()
    item_id = _item(session)
    client.post(f"/api/nuggets/{item_id}/attach", json={"task_id": task["id"]})

    # suggest button hidden without a key, and the endpoint explains
    assert "Suggest description (AI)" not in client.get(f"/gui/tasks/{task['id']}/detail").text
    assert "No LLM API key" in client.post(f"/gui/tasks/{task['id']}/description/propose").text

    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    from ewo.core.llm import FakeLLM

    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=['{"description": "the new description"}']
    )
    assert "Suggest description (AI)" in client.get(f"/gui/tasks/{task['id']}/detail").text
    preview = client.post(f"/gui/tasks/{task['id']}/description/propose")
    assert "the new description" in preview.text

    applied = client.post(
        f"/gui/tasks/{task['id']}/description/apply",
        data={"description": "the new description"},
    )
    assert "the new description" in applied.text
    assert client.get(f"/api/tasks/{task['id']}").json()["description"] == "the new description"

    # a task with no attached nuggets has nothing to describe
    bare = client.post("/api/tasks", json={"title": "bare"}).json()
    assert "no attached nuggets" in client.post(f"/gui/tasks/{bare['id']}/description/propose").text

    # garbage LLM output surfaces inline
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    assert (
        "did not return valid description"
        in client.post(f"/gui/tasks/{task['id']}/description/propose").text
    )


def test_tasks_topic_group_flow(client: TestClient, session: Session) -> None:
    # no llm key configured → the topic form is hidden, and the endpoint explains
    assert "Group topic (AI)" not in client.get("/tasks").text
    response = client.post("/gui/tasks/organize/topic", data={"topic": "website"})
    assert "No LLM API key" in response.text

    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    assert "Group topic (AI)" in client.get("/tasks").text
    blank = client.post("/gui/tasks/organize/topic", data={"topic": "  "})
    assert "Enter a topic" in blank.text

    # the proposal renders as a checkbox list with a parent choice
    redesign = client.post("/api/tasks", json={"title": "redesign website"}).json()
    copy = client.post("/api/tasks", json={"title": "write homepage copy"}).json()
    client.post("/api/tasks", json={"title": "unrelated errand"})
    from ewo.core.llm import FakeLLM

    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[
            json.dumps(
                {
                    "task_ids": [redesign["id"], copy["id"]],
                    "parent_title": "Website project",
                    "existing_parent_id": None,
                    "reason": "both about the site",
                }
            )
        ]
    )
    proposal = client.post("/gui/tasks/organize/topic", data={"topic": "website"})
    assert "redesign website" in proposal.text and "write homepage copy" in proposal.text
    assert "unrelated errand" not in proposal.text
    assert 'value="Website project"' in proposal.text

    # no related tasks → inline note instead of a proposal
    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[
            json.dumps(
                {"task_ids": [], "parent_title": None, "existing_parent_id": None, "reason": ""}
            )
        ]
    )
    nothing = client.post("/gui/tasks/organize/topic", data={"topic": "quantum"})
    assert "No tasks found" in nothing.text

    # an LLM returning garbage surfaces as an inline error, not a 500
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    errored_propose = client.post("/gui/tasks/organize/topic", data={"topic": "website"})
    assert "did not return valid topic grouping" in errored_propose.text

    # applying with a new parent refreshes the task list and clears the panel
    # (repeated child_ids fields; the vendored-httpx TestClient needs a raw body)
    form = (
        f"child_ids={redesign['id']}&child_ids={copy['id']}"
        "&parent_choice=new&parent_title=Website+project&parent_id="
    )
    applied = client.post(
        "/gui/tasks/organize/group",
        content=form,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert applied.status_code == 200
    assert "Website project" in applied.text
    assert 'id="organize-result" hx-swap-oob' in applied.text
    all_tasks = client.get("/api/tasks").json()
    parent = next(t for t in all_tasks if t["title"] == "Website project")
    assert parent["source"] == "manual"
    children = {t["title"] for t in all_tasks if t["parent_id"] == parent["id"]}
    assert children == {"redesign website", "write homepage copy"}

    # an existing parent inside the group is rejected inline, nothing changes
    bad_form = (
        f"child_ids={redesign['id']}&child_ids={copy['id']}"
        f"&parent_choice=existing&parent_title=&parent_id={redesign['id']}"
    )
    errored = client.post(
        "/gui/tasks/organize/group",
        content=bad_form,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert "part of the group" in errored.text
    assert client.get(f"/api/tasks/{copy['id']}").json()["parent_id"] == parent["id"]


def test_task_split_parts_flow(client: TestClient, session: Session) -> None:
    task = client.post("/api/tasks", json={"title": "big mixed task"}).json()
    propose_url = "/gui/tasks/organize/split/propose"
    apply_url = "/gui/tasks/organize/split/apply"

    # no llm key configured: the split form is only ever in the edit panel
    assert "Split (AI)" not in client.get(f"/gui/tasks/{task['id']}/detail").text
    assert "Split (AI)" not in client.get(f"/gui/tasks/{task['id']}/edit").text
    no_key = client.post(
        propose_url, data={"task_id": str(task["id"]), "instructions": "break it up"}
    )
    assert "No LLM API key" in no_key.text

    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    assert "Split (AI)" not in client.get(f"/gui/tasks/{task['id']}/detail").text
    assert "Split (AI)" in client.get(f"/gui/tasks/{task['id']}/edit").text
    blank = client.post(propose_url, data={"task_id": str(task["id"]), "instructions": "  "})
    assert "Enter split instructions" in blank.text

    first = _item(session, "part one")
    second = _item(session, "part two", "ai/y.md")
    client.post(f"/api/nuggets/{first}/attach", json={"task_id": task["id"]})
    client.post(f"/api/nuggets/{second}/attach", json={"task_id": task["id"]})

    # the proposal renders as a checkbox list with hidden proposal JSON + mode
    from ewo.core.llm import FakeLLM

    proposal_json = json.dumps(
        {
            "mode": "children",
            "parts": [
                {"title": "half a", "nugget_ids": [first], "reason": "one side"},
                {"title": "half b", "nugget_ids": [second], "reason": "other side"},
            ],
        }
    )
    client.app.state.llm = FakeLLM(responses=[proposal_json])  # type: ignore[attr-defined]
    proposed = client.post(
        propose_url, data={"task_id": str(task["id"]), "instructions": "break it up"}
    )
    assert "half a" in proposed.text and "half b" in proposed.text
    assert 'name="proposal"' in proposed.text and 'value="children"' in proposed.text
    assert 'name="selected"' in proposed.text

    # an LLM returning garbage surfaces as an inline error, not a 500
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    errored = client.post(propose_url, data={"task_id": str(task["id"]), "instructions": "again"})
    assert "did not return valid split parts" in errored.text

    # applying with only the first part checked refreshes the list only
    form = (
        f"task_id={task['id']}&mode=children"
        f"&proposal={urllib.parse.quote(proposal_json)}&selected=0"
    )
    applied = client.post(
        apply_url, content=form, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert applied.status_code == 200
    assert "half a" in applied.text and "half b" not in applied.text
    assert f'id="task-{task["id"]}-detail" hx-swap-oob' not in applied.text
    all_tasks = client.get("/api/tasks").json()
    [child] = [t for t in all_tasks if t["title"] == "half a"]
    assert child["parent_id"] == task["id"]
    assert client.get(f"/api/tasks/{child['id']}/nuggets").json()[0]["id"] == first
    # the unchecked part was not created and its nugget stayed on the source
    [remaining] = client.get(f"/api/tasks/{task['id']}/nuggets").json()
    assert remaining["id"] == second

    # a mode that contradicts the hidden proposal is rejected, nothing changes
    count = len(all_tasks)
    bad_mode = (
        f"task_id={task['id']}&mode=siblings"
        f"&proposal={urllib.parse.quote(proposal_json)}&selected=0"
    )
    mismatch = client.post(
        apply_url, content=bad_mode, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert "does not match" in mismatch.text
    assert len(client.get("/api/tasks").json()) == count

    # nothing selected -> inline error, no new tasks
    no_selection = client.post(
        apply_url,
        content=f"task_id={task['id']}&mode=children&proposal={urllib.parse.quote(proposal_json)}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert "no split parts selected" in no_selection.text
    assert len(client.get("/api/tasks").json()) == count


def test_task_split_from_edit_panel(client: TestClient, session: Session) -> None:
    task = client.post("/api/tasks", json={"title": "edit me"}).json()
    first = _item(session, "part one")
    client.post(f"/api/nuggets/{first}/attach", json={"task_id": task["id"]})

    # hidden without an LLM key, visible with one
    assert "Split (AI)" not in client.get(f"/gui/tasks/{task['id']}/edit").text
    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    edit_form = client.get(f"/gui/tasks/{task['id']}/edit")
    assert "Split (AI)" in edit_form.text

    # an edit-panel error renders inline without re-including the detail panel
    blank = client.post(
        "/gui/tasks/organize/split/propose",
        data={"task_id": str(task["id"]), "instructions": "  "},
    )
    assert "Enter split instructions" in blank.text
    assert "Split #" not in blank.text
    assert "Sub-tasks" not in blank.text  # _task_detail.html was not re-included

    from ewo.core.llm import FakeLLM

    proposal_json = json.dumps(
        {"mode": "children", "parts": [{"title": "half a", "nugget_ids": [first]}]}
    )
    client.app.state.llm = FakeLLM(responses=[proposal_json])  # type: ignore[attr-defined]
    proposed = client.post(
        "/gui/tasks/organize/split/propose",
        data={"task_id": str(task["id"]), "instructions": "break it up"},
    )
    assert "half a" in proposed.text
    assert 'name="proposal"' in proposed.text


def test_related_grouping_flow(client: TestClient, session: Session) -> None:
    related_url = "/gui/tasks/organize/related"

    # no llm key configured: creating a task offers no banner, and the
    # related endpoint explains instead of calling the LLM
    created = client.post("/gui/tasks", data={"title": "no-key task"})
    assert "Find related tasks (AI)?" not in created.text
    no_key = client.post(related_url, data={"task_id": "1"})
    assert "No LLM API key" in no_key.text

    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]

    # with a key, creating a task via the full form returns the list plus an
    # out-of-band banner offering to find related tasks
    dash = client.post("/api/tasks", json={"title": "redesign sla dashboard"}).json()
    pipe = client.post("/api/tasks", json={"title": "fix sla data pipeline"}).json()
    created = client.post("/gui/tasks", data={"title": "ix sla report overhaul"})
    assert "Find related tasks (AI)?" in created.text
    assert 'id="related-banner" hx-swap-oob' in created.text
    new_task = next(
        t for t in client.get("/api/tasks").json() if t["title"] == "ix sla report overhaul"
    )

    # an unknown task id surfaces as an inline error, not a 500
    missing = client.post(related_url, data={"task_id": "9999"})
    assert "task 9999 not found" in missing.text

    # first POST: the LLM asks clarifying questions, rendered with answer
    # inputs and the questions round-tripping in hidden fields
    from ewo.core.llm import FakeLLM

    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[json.dumps({"questions": ["Public dashboard?", "Which quarter?"]})]
    )
    questions = client.post(related_url, data={"task_id": str(new_task["id"])})
    assert "Public dashboard?" in questions.text and "Which quarter?" in questions.text
    assert 'name="question"' in questions.text and 'name="answer"' in questions.text
    assert 'name="answered"' in questions.text

    # an LLM returning garbage surfaces as an inline error, not a 500
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    errored = client.post(related_url, data={"task_id": str(new_task["id"])})
    assert "did not return valid related questions" in errored.text

    # resubmitting with answers proposes a topic grouping for review
    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[
            json.dumps(
                {
                    "task_ids": [new_task["id"], dash["id"], pipe["id"]],
                    "parent_title": "SLA reporting",
                    "existing_parent_id": None,
                    "reason": "same report work",
                }
            )
        ]
    )
    form = (
        f"task_id={new_task['id']}&answered=1"
        "&question=Public+dashboard%3F&answer=yes"
        "&question=Which+quarter%3F&answer=Q4"
    )
    review = client.post(
        related_url, content=form, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert 'value="SLA reporting"' in review.text
    assert "redesign sla dashboard" in review.text and "fix sla data pipeline" in review.text
    assert 'name="child_ids"' in review.text

    # no related tasks -> inline note instead of a proposal
    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[
            json.dumps(
                {"task_ids": [], "parent_title": None, "existing_parent_id": None, "reason": ""}
            )
        ]
    )
    nothing = client.post(
        related_url, content=form, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert "No tasks found" in nothing.text

    # the answered path with no API key explains; a bad LLM reply errors inline
    saved_key = client.app.state.config.llm.api_key  # type: ignore[attr-defined]
    client.app.state.config.llm.api_key = ""  # type: ignore[attr-defined]
    no_key_qa = client.post(
        related_url, content=form, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert "No LLM API key" in no_key_qa.text
    client.app.state.config.llm.api_key = saved_key  # type: ignore[attr-defined]
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    bad_qa = client.post(
        related_url, content=form, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert "did not return valid related grouping" in bad_qa.text

    # applying the confirmed grouping reuses the shared group route
    apply_form = (
        f"child_ids={new_task['id']}&child_ids={dash['id']}&child_ids={pipe['id']}"
        "&parent_choice=new&parent_title=SLA+reporting&parent_id="
    )
    applied = client.post(
        "/gui/tasks/organize/group",
        content=apply_form,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert applied.status_code == 200
    assert "SLA reporting" in applied.text
    all_tasks = client.get("/api/tasks").json()
    parent = next(t for t in all_tasks if t["title"] == "SLA reporting")
    children = {t["title"] for t in all_tasks if t["parent_id"] == parent["id"]}
    assert children == {"ix sla report overhaul", "redesign sla dashboard", "fix sla data pipeline"}


# --- labels (tags) ---


def test_labels_page_crud(client: TestClient) -> None:
    page = client.get("/labels")
    assert page.status_code == 200 and "No labels yet" in page.text

    created = client.post("/gui/labels", data={"name": "Infra ", "description": "platform work"})
    assert "infra" in created.text and "platform work" in created.text

    [tag] = client.get("/api/tags").json()
    assert tag["name"] == "infra" and tag["description"] == "platform work"

    edit_form = client.get(f"/gui/labels/{tag['id']}/edit")
    assert 'value="infra"' in edit_form.text

    updated = client.post(
        f"/gui/labels/{tag['id']}", data={"name": "Platform", "description": "new desc"}
    )
    assert "platform" in updated.text and "new desc" in updated.text

    deleted = client.post(f"/gui/labels/{tag['id']}/delete")
    assert "No labels yet" in deleted.text


def test_label_color_picker_and_pillbox(client: TestClient) -> None:
    created = client.post("/gui/labels", data={"name": "urgent", "color": "#ee7733"})
    assert "background-color: #EE7733" in created.text
    assert "#EE7733  #EE7733" not in created.text

    [tag] = client.get("/api/tags").json()
    assert tag["color"] == "#EE7733"

    edit_form = client.get(f"/gui/labels/{tag['id']}/edit")
    assert "#EE7733" in edit_form.text  # swatch selected

    # the task list renders the label as a colored pillbox
    client.post("/api/tasks", json={"title": "labeled", "tags": ["urgent"]})
    listing = client.get("/gui/tasks")
    assert "background-color: #EE7733" in listing.text


def test_tasks_multi_label_filter(client: TestClient) -> None:
    client.post("/api/tasks", json={"title": "only a", "tags": ["a"]})
    client.post("/api/tasks", json={"title": "only b", "tags": ["b"]})
    client.post("/api/tasks", json={"title": "both", "tags": ["a", "b"]})

    page = client.get("/tasks")
    assert "any selected label" in page.text and "all selected labels" in page.text

    any_match = client.get("/gui/tasks", params=[("tags", "a"), ("tags", "b")])
    assert "only a" in any_match.text and "only b" in any_match.text and "both" in any_match.text

    all_match = client.get(
        "/gui/tasks", params=[("tags", "a"), ("tags", "b"), ("tag_match", "all")]
    )
    assert "both" in all_match.text
    assert "only a" not in all_match.text and "only b" not in all_match.text


def test_tag_sync_banner_and_propagation(client: TestClient) -> None:
    parent = client.post("/api/tasks", json={"title": "parent", "tags": ["base"]}).json()
    child = client.post(
        "/api/tasks", json={"title": "child", "parent_id": parent["id"], "tags": ["extra"]}
    ).json()

    # inheritance on create: the child already carries the parent's label
    child_tags = client.get(f"/api/tasks/{child['id']}").json()["tags"]
    assert sorted(t["name"] for t in child_tags) == ["base", "extra"]

    # adding a label to a task with descendants offers propagation (OOB banner)
    response = client.post(
        f"/gui/tasks/{parent['id']}",
        data={"title": "parent", "priority": "normal", "status": "open", "tags": "base, new"},
    )
    assert "Apply to sub-tasks" in response.text
    assert 'hx-swap-oob="innerHTML"' in response.text

    # nothing propagated until confirmed
    child_tags = client.get(f"/api/tasks/{child['id']}").json()["tags"]
    assert "new" not in [t["name"] for t in child_tags]

    propagated = client.post(f"/gui/tasks/{parent['id']}/propagate-tags", data={"names": "new"})
    assert propagated.status_code == 200
    child_tags = client.get(f"/api/tasks/{child['id']}").json()["tags"]
    assert sorted(t["name"] for t in child_tags) == ["base", "extra", "new"]  # extras survive


def test_tag_sync_banner_not_shown_without_changes(client: TestClient) -> None:
    parent = client.post("/api/tasks", json={"title": "parent", "tags": ["base"]}).json()
    client.post("/api/tasks", json={"title": "child", "parent_id": parent["id"]})

    # no new labels -> no banner
    same = client.post(
        f"/gui/tasks/{parent['id']}",
        data={"title": "parent", "priority": "normal", "status": "open", "tags": "base"},
    )
    assert "Apply to sub-tasks" not in same.text

    # new label but no descendants -> no banner
    solo = client.post("/api/tasks", json={"title": "solo"}).json()
    added = client.post(
        f"/gui/tasks/{solo['id']}",
        data={"title": "solo", "priority": "normal", "status": "open", "tags": "z"},
    )
    assert "Apply to sub-tasks" not in added.text

    # an empty propagation request is a no-op that re-renders the list
    response = client.post(f"/gui/tasks/{parent['id']}/propagate-tags", data={"names": ""})
    assert response.status_code == 200 and "parent" in response.text


# --- inbox ---


def test_inbox_page_and_actions(client: TestClient, session: Session) -> None:
    item_id = _item(session)
    other = _item(session, "budget", "ai/y.md")

    page = client.get("/inbox")
    assert page.status_code == 200
    assert "chase Matti" in page.text and "eurohpc/x.md" in page.text
    assert "notes disabled in config" in page.text
    assert "Never scanned" in page.text
    assert "No suggested task" in page.text  # nothing matched yet
    assert "break-words" in page.text  # unbroken text can't blow out the width
    assert "break-all" in page.text  # excerpts/tokens force-break even without whitespace
    assert "min-w-0" in page.text  # grid/flex items can shrink below content width

    # choosing "new task" opens a pre-filled create dialog instead of attaching
    dialog = client.post(
        f"/gui/nuggets/{item_id}/attach",
        data={"task_id": "", "priority": "high", "assignee_id": "", "due_date": "2026-10-01"},
    )
    assert "New task from" in dialog.text and "chase Matti" in dialog.text
    new_ids = [n["id"] for n in client.get("/api/nuggets", params={"status": "new"}).json()]
    assert item_id in new_ids

    # cancel returns to the plain attach form
    cancel = client.get(f"/gui/nuggets/{item_id}/attach-form")
    assert "→ new task" in cancel.text

    created = client.post(
        f"/gui/nuggets/{item_id}/create",
        data={"title": "chase Matti", "priority": "high", "due_date": "2026-10-01"},
    )
    assert "attached" in created.text and "task #" in created.text
    task = client.get("/api/tasks").json()[0]
    assert task["title"] == "chase Matti" and task["priority"] == "high"
    assert task["external_links"][0]["external_key"] == f"eurohpc/x.md:34:{item_id}"

    done = client.post(f"/gui/nuggets/{other}/done")
    assert "already done" in done.text

    assert "Nothing to review" in client.get("/gui/inbox").text
    assert "budget" in client.get("/gui/inbox", params={"status": "all"}).text
    assert "budget" in client.get("/gui/inbox", params={"status": "already_done"}).text


def test_inbox_attach_to_existing_and_group(client: TestClient, session: Session) -> None:
    task = client.post("/api/tasks", json={"title": "the topic"}).json()
    item_id = _item(session)
    other = _item(session, "budget", "ai/y.md")

    # a suggestion groups the nugget under its task with a bulk button
    nuggets.update_nugget(session, item_id, suggested_task_id=task["id"])
    page = client.get("/inbox")
    assert f"→ #{task['id']} the topic" in page.text
    assert "Attach selected to #" in page.text
    assert "✓ Attach to #" in page.text  # one-click shortcut appears when suggested

    # single attach to the existing task via the picker
    attached = client.post(f"/gui/nuggets/{item_id}/attach", data={"task_id": str(task["id"])})
    assert "attached" in attached.text
    assert client.get(f"/api/tasks/{task['id']}/nuggets").json()[0]["id"] == item_id

    # bulk attach for the remaining group
    nuggets.update_nugget(session, other, suggested_task_id=task["id"])
    response = client.post("/gui/inbox/attach-group", data={"task_id": str(task["id"])})
    assert "Nothing to review" in response.text
    assert len(client.get(f"/api/tasks/{task['id']}/nuggets").json()) == 2


def test_inbox_attach_group_selection(client: TestClient, session: Session) -> None:
    task = client.post("/api/tasks", json={"title": "pick some"}).json()
    first = _item(session, "keep me", "ai/k.md")
    second = _item(session, "skip me", "ai/s.md")
    nuggets.update_nugget(session, first, suggested_task_id=task["id"])
    nuggets.update_nugget(session, second, suggested_task_id=task["id"])

    # only the checked nugget is attached (nugget_ids excludes `second`)
    client.post(
        "/gui/inbox/attach-group",
        data={"task_id": str(task["id"]), "selected": "1", "nugget_ids": str(first)},
    )
    attached = client.get(f"/api/tasks/{task['id']}/nuggets").json()
    assert [n["id"] for n in attached] == [first]
    # the unchecked nugget stays new and is still suggested for the task
    assert client.get("/api/nuggets").json()[0]["id"] == second

    # unchecking everything (no nugget_ids) attaches nothing
    client.post(
        "/gui/inbox/attach-group",
        data={"task_id": str(task["id"]), "selected": "1"},
    )
    assert [n["id"] for n in client.get(f"/api/tasks/{task['id']}/nuggets").json()] == [first]


def test_inbox_suggest_creates_and_create_group(client: TestClient, session: Session) -> None:
    a = _item(session, "alpha thing", "ai/a.md")
    b = _item(session, "beta thing", "ai/b.md")

    assert "No LLM API key" in client.post("/gui/inbox/suggest-creates").text

    client.app.state.config.llm.api_key = "sk"  # type: ignore[attr-defined]
    from ewo.core.llm import FakeLLM

    client.app.state.llm = FakeLLM(  # type: ignore[attr-defined]
        responses=[
            json.dumps(
                {
                    "proposals": [
                        {
                            "kind": "create",
                            "nugget_ids": [a, b],
                            "title": "alpha beta",
                            "reason": "pair",
                        }
                    ]
                }
            )
        ]
    )
    proposal = client.post("/gui/inbox/suggest-creates")
    assert "alpha beta" in proposal.text and "alpha thing" in proposal.text

    # untick one nugget: only the selected one becomes the new task
    created = client.post(
        "/gui/inbox/create-group", data={"title": "alpha beta", "nugget_ids": str(a)}
    )
    assert created.status_code == 200
    assert client.get("/api/nuggets", params={"status": "attached"}).json()[0]["id"] == a
    assert [n["id"] for n in client.get("/api/nuggets").json()] == [b]

    # no nuggets selected → inline error, nothing created
    response = client.post("/gui/inbox/create-group", data={"title": "alpha beta"})
    assert "Select at least one note" in response.text

    # empty title → inline error
    empty = client.post("/gui/inbox/create-group", data={"title": "   ", "nugget_ids": str(b)})
    assert "New task needs a title" in empty.text

    # garbage LLM output surfaces inline, not a 500
    client.app.state.llm = FakeLLM(responses=["garbage"])  # type: ignore[attr-defined]
    assert (
        "did not return valid inbox create proposals"
        in client.post("/gui/inbox/suggest-creates").text
    )


def test_inbox_suggest_button(client: TestClient, session: Session) -> None:
    _item(session)
    # no llm key configured: the job fails but the inbox still re-renders
    response = client.post("/gui/inbox/suggest", data={"status": "new", "owner": ""})
    assert response.status_code == 200
    assert "chase Matti" in response.text


def test_inbox_dismiss_and_owner_filter(client: TestClient, session: Session) -> None:
    person = client.post("/api/people", json={"name": "Neda"}).json()
    item, _ = nuggets.upsert_nugget(
        session, "swd/x.md", 3, "fix pipeline", NuggetKind.ACTION, owner_id=person["id"]
    )
    session.commit()
    _item(session, "unowned", "misc.md")

    mine = client.get("/gui/inbox", params={"owner": str(person["id"])})
    assert "fix pipeline" in mine.text and "unowned" not in mine.text

    dismissed = client.post(f"/gui/nuggets/{item.id}/dismiss")
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
