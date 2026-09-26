"""LLM extraction of outstanding items and the nuggets inbox service layer."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

import ewo.jobs.builtin  # noqa: F401 - registers builtins
from ewo.config import Config, NotesConfig
from ewo.core import nuggets, people, tasks
from ewo.core.llm import FakeLLM
from ewo.core.note_extract import (
    ExtractionResult,
    extract_items,
    last_successful_scan,
    roster_text,
    scan_notes,
)
from ewo.core.people import NotFoundError
from ewo.db.models import (
    JobRun,
    JobRunStatus,
    NuggetKind,
    NuggetStatus,
    TaskPriority,
    TaskSource,
)
from ewo.jobs.registry import registry

TODAY = date(2026, 9, 21)


def _items(*items: dict[str, object]) -> str:
    return json.dumps({"items": list(items)})


def _notes_config(notes_root: Path, **overrides: object) -> NotesConfig:
    return NotesConfig(enabled=True, root=notes_root, **overrides)  # type: ignore[arg-type]


# --- extraction ---


def test_extract_items_parses_and_uses_context() -> None:
    llm = FakeLLM(responses=[_items({"summary": "Do X", "line": 3, "owner": "Erik"})])
    result, tokens = extract_items(llm, "CTX", "- Erik", "a.md", "1: a\n2: b\n3: do x", TODAY)
    assert result.items[0].summary == "Do X"
    assert result.items[0].kind == NuggetKind.ACTION
    assert tokens == 10
    call = llm.calls[0]
    assert "CTX" in str(call["system"]) and call["smart"] is True
    assert "Today is 2026-09-21" in str(call["prompt"]) and "3: do x" in str(call["prompt"])


def test_extract_items_strips_fences_and_retries() -> None:
    llm = FakeLLM(
        responses=["not json", "```json\n" + _items({"summary": "Y", "line": 1}) + "\n```"]
    )
    result, tokens = extract_items(llm, "", "", "a.md", "1: y", TODAY)
    assert result.items[0].summary == "Y"
    assert tokens == 20
    assert "not valid JSON" in str(llm.calls[1]["prompt"])


def test_extract_items_gives_up_after_retry() -> None:
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(ValueError, match="did not return valid items"):
        extract_items(llm, "", "", "a.md", "1: y", TODAY)


def test_extraction_result_rejects_bad_line() -> None:
    with pytest.raises(ValueError):
        ExtractionResult.model_validate_json(_items({"summary": "Y", "line": 0}))


def test_extraction_jira_field_robustness() -> None:
    result = ExtractionResult.model_validate_json(
        _items({"summary": "Y", "line": 1, "jira": "PROJ-1"})  # not a list → ignored
    )
    assert result.items[0].jira == []
    result = ExtractionResult.model_validate_json(
        _items({"summary": "Y", "line": 1, "jira": ["proj-1 ", 42, "PROJ-1"]})
    )
    assert result.items[0].jira == ["PROJ-1"]


def test_roster_text(session: Session) -> None:
    people.create_person(session, "Erik", is_self=True)
    people.create_person(session, "James L", aliases=["James"])
    text = roster_text(people.list_people(session))
    assert "- Erik (the manager" in text
    assert "- James L — also written as: James" in text
    assert roster_text([]) == "- (no roster configured)"


# --- scan ---


def test_scan_notes_full_window(session: Session, notes_root: Path) -> None:
    me = people.create_person(session, "Erik", is_self=True)
    james_l = people.create_person(session, "James L", notes_dir="swd/people/james_l")
    # files are visited in sorted path order: TODO.md, eurohpc/..., james_l/2026-..., james_l/old.md
    responses = [
        _items(),
        _items(
            {"summary": "Chase ACSA acceptance", "line": 6, "owner": "Erik", "kind": "risk"},
        ),
        _items({"summary": "Fix the IX SLA report", "line": 3, "owner": "James"}),
        _items({"summary": "Recent item", "line": 5, "owner": None, "due_date": "2026-10-01"}),
    ]
    llm = FakeLLM(responses=responses)
    config = _notes_config(notes_root, exclude=["eurohpc/ignored.md", "swd/people/former"])

    summary = scan_notes(session, llm, config, since=None, job_run_id=None, today=TODAY)
    assert summary.files == 4  # TODO.md, oam, james 1:1, james old.md (windowed)
    assert summary.created == 3 and summary.seen == 0 and summary.errors == []
    assert summary.tokens == 40
    assert summary.as_text() == "files=4 new=3 seen=0 tokens=40"

    items = {i.summary: i for i in nuggets.list_nuggets(session)}
    assert items["Chase ACSA acceptance"].owner_id == me.id
    assert items["Chase ACSA acceptance"].kind == NuggetKind.RISK
    assert items["Chase ACSA acceptance"].excerpt == "- ACSA acceptance testing not confirmed"
    # bare "James" inside james_l/ resolves via folder
    assert items["Fix the IX SLA report"].owner_id == james_l.id
    assert items["Recent item"].due_date == date(2026, 10, 1)
    assert items["Recent item"].owner_id == james_l.id  # folder attribution even without name

    # old.md only sent in-window sections, with original line numbers
    old_prompt = str(llm.calls[3]["prompt"])
    assert "5: - recent item" in old_prompt and "ancient" not in old_prompt
    assert "AGENTS.md" not in old_prompt


def test_scan_notes_dedupes_and_respects_review(session: Session, notes_root: Path) -> None:
    config = _notes_config(notes_root, exclude=["eurohpc", "TODO.md", "swd/people/former"])
    llm = FakeLLM(responses=[_items({"summary": "Fix the IX SLA report", "line": 3}), _items()])
    first = scan_notes(session, llm, config, since=None, today=TODAY)
    assert first.created == 1
    item = nuggets.list_nuggets(session)[0]
    nuggets.set_nugget_status(session, item.id, NuggetStatus.ALREADY_DONE)

    llm2 = FakeLLM(responses=[_items({"summary": "fix the IX SLA report!", "line": 4}), _items()])
    second = scan_notes(session, llm2, config, since=None, today=TODAY)
    assert second.created == 0 and second.seen == 1
    assert nuggets.list_nuggets(session) == []  # still not re-surfaced
    refreshed = nuggets.get_nugget(session, item.id)
    assert refreshed.status == NuggetStatus.ALREADY_DONE and refreshed.line == 4


def test_scan_notes_captures_jira_keys(session: Session, notes_root: Path) -> None:
    """Jira keys come from the LLM's structured field and from the raw text."""
    config = _notes_config(notes_root, exclude=["eurohpc", "TODO.md", "swd/people/former"])
    llm = FakeLLM(
        responses=[
            _items(
                {"summary": "Send the Jira ticket", "line": 3, "jira": ["chrn-222", "bad"]},
            ),
            _items(),
        ]
    )
    summary = scan_notes(session, llm, config, since=None, today=TODAY)
    assert summary.created == 1
    [item] = nuggets.list_nuggets(session)
    # LLM-provided key normalised, invalid dropped, regex adds keys from the excerpt
    assert item.jira_keys == ["CHRN-222"]

    # a re-scan (same summary → same fingerprint) mentioning another key unions them
    llm2 = FakeLLM(
        responses=[
            _items({"summary": "Send the Jira ticket", "line": 3, "jira": ["DBOARD3-1111"]}),
            _items(),
        ]
    )
    second = scan_notes(session, llm2, config, since=None, today=TODAY)
    assert second.created == 0 and second.seen == 1
    assert nuggets.get_nugget(session, item.id).jira_keys == ["CHRN-222", "DBOARD3-1111"]


def test_scan_notes_records_errors_and_truncates(session: Session, notes_root: Path) -> None:
    config = _notes_config(notes_root, exclude=["eurohpc", "swd", "TODO.md"], max_file_chars=5)
    (notes_root / "misc.md").write_text("- something long enough to be truncated\n")
    (notes_root / "empty.md").write_text("\n\n")  # nothing to extract -> not sent
    llm = FakeLLM(responses=["garbage"])
    summary = scan_notes(session, llm, config, since=None, today=TODAY)
    assert summary.files == 1 and summary.errors and "misc.md" in summary.errors[0]
    assert summary.as_text().endswith("errors=1")
    assert len(str(llm.calls[0]["prompt"]).split("Note `misc.md`:\n\n")[1]) == 5


def test_scan_notes_incremental_skips_unchanged(session: Session, notes_root: Path) -> None:
    config = _notes_config(notes_root)
    llm = FakeLLM(responses=[_items()])
    summary = scan_notes(
        session, llm, config, since=datetime.now() + timedelta(days=1), today=TODAY
    )
    assert summary.files == 0 and llm.calls == []


def test_last_successful_scan(session: Session) -> None:
    assert last_successful_scan(session) is None
    session.add(JobRun(job_name="notes_scan", status=JobRunStatus.FAILED))
    session.add(
        JobRun(job_name="notes_scan", status=JobRunStatus.SUCCESS, started_at=datetime(2026, 9, 1))
    )
    session.add(
        JobRun(job_name="notes_scan", status=JobRunStatus.SUCCESS, started_at=datetime(2026, 9, 5))
    )
    session.commit()
    assert last_successful_scan(session) == datetime(2026, 9, 5)


# --- job ---


def test_notes_scan_job(
    session_factory: sessionmaker[Session], config: Config, notes_root: Path
) -> None:
    run = registry.run("notes_scan", session_factory, config, FakeLLM())
    assert run.status == JobRunStatus.SUCCESS and run.result == "notes disabled"

    config.notes.enabled = True
    config.notes.root = notes_root
    run = registry.run("notes_scan", session_factory, config, FakeLLM())
    assert run.status == JobRunStatus.FAILED and "llm.api_key" in str(run.error)

    config.llm.api_key = "sk"
    config.notes.exclude = ["eurohpc", "swd", "TODO.md"]
    (notes_root / "misc.md").write_text("- open thing\n")
    llm = FakeLLM(responses=[_items({"summary": "Open thing", "line": 1})])
    run = registry.run("notes_scan", session_factory, config, llm)
    assert run.status == JobRunStatus.SUCCESS
    assert run.result is not None and run.result.startswith("files=1 new=1")
    assert "reviewed=1" in run.result  # the matching step ran too
    assert run.tokens_used == 10
    with session_factory() as session:
        assert nuggets.list_nuggets(session)[0].job_run_id == run.id

    # every file failing => the run fails
    bad = FakeLLM(responses=["garbage"])
    run = registry.run("notes_scan", session_factory, config, bad, params={"full": "true"})
    assert run.status == JobRunStatus.FAILED and "misc.md" in str(run.error)


# --- inbox service ---


def test_attach_nugget_creates_linked_task(session: Session) -> None:
    owner = people.create_person(session, "Neda")
    item, created = nuggets.upsert_nugget(
        session,
        "swd/x.md",
        7,
        "Add SSO to glitchtip",
        NuggetKind.ACTION,
        excerpt="- Glitchtip: add SSO",
        owner_id=owner.id,
        due_date=date(2026, 10, 1),
    )
    session.commit()
    assert created
    task = nuggets.attach_nugget(session, item.id, priority=TaskPriority.HIGH)
    assert task.source == TaskSource.NOTES
    assert task.assignee_id == owner.id and task.due_date == date(2026, 10, 1)
    assert task.description == "- Glitchtip: add SSO"
    assert tasks.find_link(session, "notes", f"swd/x.md:7:{item.id}") is not None
    assert item.status == NuggetStatus.ATTACHED and item.task_id == task.id
    # attaching twice returns the same task
    assert nuggets.attach_nugget(session, item.id).id == task.id

    # overrides
    other, _ = nuggets.upsert_nugget(session, "swd/y.md", 1, "Other", NuggetKind.QUESTION)
    session.commit()
    task2 = nuggets.attach_nugget(
        session, other.id, assignee_id=None, due_date=date(2026, 12, 1), title="Renamed"
    )
    assert task2.title == "Renamed" and task2.due_date == date(2026, 12, 1)


def test_attach_nugget_two_items_same_line(session: Session) -> None:
    """Two distinct nuggets extracted from the same path:line must not collide
    on the external_links unique constraint when both are attached."""
    first, _ = nuggets.upsert_nugget(session, "swd/x.md", 53, "First action", NuggetKind.ACTION)
    second, _ = nuggets.upsert_nugget(session, "swd/x.md", 53, "Second action", NuggetKind.ACTION)
    session.commit()
    assert first.id != second.id

    task1 = nuggets.attach_nugget(session, first.id)
    task2 = nuggets.attach_nugget(session, second.id)
    assert task1.id != task2.id
    assert tasks.find_link(session, "notes", f"swd/x.md:53:{first.id}").task_id == task1.id
    assert tasks.find_link(session, "notes", f"swd/x.md:53:{second.id}").task_id == task2.id


def test_list_filters_count_and_update(session: Session) -> None:
    owner = people.create_person(session, "Neda")
    a, _ = nuggets.upsert_nugget(session, "swd/a.md", 1, "A", NuggetKind.ACTION, owner_id=owner.id)
    b, _ = nuggets.upsert_nugget(session, "eurohpc/b.md", 1, "B", NuggetKind.ACTION)
    session.commit()
    assert nuggets.count_new(session) == 2
    assert [i.id for i in nuggets.list_nuggets(session, owner_id=owner.id)] == [a.id]
    assert [i.id for i in nuggets.list_nuggets(session, path_prefix="eurohpc/")] == [b.id]
    nuggets.set_nugget_status(session, b.id, NuggetStatus.DISMISSED)
    assert nuggets.count_new(session) == 1
    assert len(nuggets.list_nuggets(session, status=None)) == 2

    updated = nuggets.update_nugget(session, a.id, summary="A2", kind=NuggetKind.DEADLINE)
    assert updated.summary == "A2" and updated.kind == NuggetKind.DEADLINE

    with pytest.raises(NotFoundError):
        nuggets.get_nugget(session, 999)
