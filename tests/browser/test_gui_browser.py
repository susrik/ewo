"""Browser (Playwright) smoke tests for the htmx GUI.

One focused test per core documented flow, asserting on the *rendered* page and
the data the server returns (no vision/image assertions). These complement the
TestClient suite in ``tests/test_gui.py``, which pins the same behaviour at the
fragment level; here we exercise real browser rendering and interaction: htmx
fragment swaps, form submission, checkbox selection and the SSE job-log stream.

Seeding goes through the live server's HTTP API (tasks/people/labels) or, for
nuggets (which only ever arrive via the notes pipeline), directly through the
shared SQLite store exposed by the ``live_server`` fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from conftest import LiveServer
    from playwright.sync_api import Page

from ewo.core import nuggets
from ewo.db.models import NuggetKind

pytestmark = pytest.mark.browser

# Playwright is imported lazily so a plain ``pytest`` run (which deselects the
# ``browser`` marker) never needs it installed. It is skipped entirely when
# missing; install via ``pip install -e ".[test-browser]"`` + ``playwright install chromium``.
pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402


def _seed_nugget(live_server: LiveServer, summary: str, path: str = "eurohpc/x.md") -> int:
    with live_server.session_factory() as session:
        item, _ = nuggets.upsert_nugget(
            session, path, 34, summary, NuggetKind.ACTION, f"- {summary}"
        )
        session.commit()
        return item.id


def _api_tasks(live_server: LiveServer, **params: object) -> list[dict]:
    return httpx.get(f"{live_server.base_url}/api/tasks", params=params).json()


def _settle_htmx(page: Page) -> None:
    """Wait until htmx has finished swapping/settling.

    htmx attaches ``hx-*`` listeners to freshly-swapped content during its
    settle phase (after the elements are already visible), so interacting with a
    just-swapped control without waiting can race: a ``change``/click fires
    before htmx has wired the handler and the request is silently dropped. Poll
    until no in-flight request, swap, or settle state remains.
    """
    page.wait_for_function(
        "!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def test_dashboard_quick_add(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.base_url}/")
    expect(page.get_by_placeholder("What needs doing?")).to_be_visible()

    page.get_by_placeholder("What needs doing?").fill("write the report")
    page.get_by_role("button", name="Add", exact=True).click()

    # htmx swaps the confirmation fragment into the result container …
    expect(page.locator("#quick-add-result")).to_contain_text("write the report")
    # … and the task really landed server-side
    assert "write the report" in {t["title"] for t in _api_tasks(live_server)}


def test_tasks_create_and_mark_done(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.base_url}/tasks")

    page.get_by_placeholder("Task title").fill("browser task one")
    page.get_by_role("button", name="Add", exact=True).click()
    _settle_htmx(page)

    row = page.locator("tr", has_text="browser task one")
    expect(row).to_be_visible()

    # mark done via the row's inline status select (htmx re-renders the row)
    row.locator("select[name=status]").select_option("done")
    expect(row).to_contain_class("opacity-50")
    task = next(
        t
        for t in _api_tasks(live_server, include_closed="true")
        if t["title"] == "browser task one"
    )
    assert task["status"] == "done"


def test_labels_create(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.base_url}/labels")

    page.get_by_placeholder("Label name").fill("Infra")
    page.get_by_placeholder("Description (optional)").fill("platform work")
    page.get_by_role("button", name="Add", exact=True).click()

    row = page.locator("tr", has_text="platform work")
    expect(row).to_be_visible()
    # the name is normalized to lowercase on the server and shown back
    expect(row).to_contain_text("infra")

    tags = httpx.get(f"{live_server.base_url}/api/tags").json()
    assert tags[0]["name"] == "infra" and tags[0]["description"] == "platform work"


def test_label_propagation_to_subtasks(page: Page, live_server: LiveServer) -> None:
    parent = httpx.post(
        f"{live_server.base_url}/api/tasks", json={"title": "parent alpha", "tags": ["base"]}
    ).json()
    httpx.post(
        f"{live_server.base_url}/api/tasks",
        json={"title": "child alpha", "parent_id": parent["id"]},
    )

    page.goto(f"{live_server.base_url}/tasks")
    parent_row = page.locator("tr", has_text="parent alpha")

    # add a second label to the parent via its edit form
    parent_row.get_by_role("button", name="edit").click()
    _settle_htmx(page)
    # the edit form swaps into #task-list; the title now lives in an <input>,
    # so target the form's tags field (unique within the task list) directly
    page.locator("#task-list input[name=tags]").fill("base, new")
    page.locator("#task-list").get_by_role("button", name="Save").click()
    _settle_htmx(page)

    # the out-of-band propagation banner is offered …
    banner = page.locator("#tag-sync-banner")
    expect(banner).to_contain_text("Apply to sub-tasks")
    # … and confirming propagates the label to the child
    banner.get_by_role("button", name="Apply to sub-tasks").click()
    expect(page.locator("tr", has_text="child alpha")).to_contain_text("new")

    [child] = [t for t in _api_tasks(live_server) if t["title"] == "child alpha"]
    assert sorted(t["name"] for t in child["tags"]) == ["base", "new"]


def test_inbox_bulk_dismiss(page: Page, live_server: LiveServer) -> None:
    _seed_nugget(live_server, "chase Matti")
    _seed_nugget(live_server, "fix the pipeline", "ai/y.md")

    page.goto(f"{live_server.base_url}/inbox")
    expect(page.locator(".nugget-row")).to_have_count(2)

    # select all visible nuggets, then dismiss in bulk
    page.locator(".select-all").check()
    expect(page.locator("#inbox-selected-count")).to_contain_text("2 selected")
    page.locator(".bulk-toolbar").get_by_role("button", name="Dismiss").click()

    expect(page.locator("#inbox-list")).to_contain_text("Nothing to review")
    assert httpx.get(f"{live_server.base_url}/api/nuggets").json() == []  # no longer "new"
    dismissed = httpx.get(
        f"{live_server.base_url}/api/nuggets", params={"status": "dismissed"}
    ).json()
    assert len(dismissed) == 2


def test_people_add_and_mark_self(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.base_url}/people")

    page.get_by_placeholder("Name").fill("Anna")
    page.get_by_placeholder("aliases, comma separated").fill("Annie, A")
    page.get_by_role("button", name="Add", exact=True).click()

    row = page.locator("tr", has_text="Anna")
    expect(row).to_contain_text("Annie, A")

    # mark the person as "me"
    row.get_by_role("button", name="this is me").click()
    expect(page.locator("tr", has_text="Anna")).to_contain_text("me")

    person = httpx.get(f"{live_server.base_url}/api/people").json()[0]
    assert person["is_self"] is True


def test_jobs_run_streams_log(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.base_url}/jobs")

    # run `what_next`; the live log view streams the run's entries over SSE
    page.locator("tr", has_text="what_next").get_by_role("button", name="run now").click()

    view = page.locator("#job-log-view")
    expect(view).to_contain_text("what_next")
    # "started"/"done" arrive only via the SSE replay (the initial render shows
    # "No log entries"), proving the EventSource connected and streamed the log
    expect(view).to_contain_text("started", timeout=15_000)
    expect(view).to_contain_text("done", timeout=15_000)
