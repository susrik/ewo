"""Nugget attach/move/detach, task suggestions, and AI task organization."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from ewo.core import nuggets, people, task_organize, tasks
from ewo.core.llm import FakeLLM
from ewo.core.nugget_match import suggest_matches
from ewo.core.people import NotFoundError
from ewo.db.models import (
    Nugget,
    NuggetKind,
    NuggetStatus,
    TaskPriority,
    TaskSource,
    TaskStatus,
)


def _nugget(
    session: Session,
    summary: str,
    path: str = "swd/x.md",
    line: int = 1,
    jira_keys: list[str] | None = None,
) -> Nugget:
    nugget, _ = nuggets.upsert_nugget(
        session, path, line, summary, NuggetKind.ACTION, jira_keys=jira_keys
    )
    session.commit()
    return nugget


# --- attach to an existing task / move / detach ---


def test_attach_to_existing_task(session: Session) -> None:
    task = tasks.create_task(session, "existing topic")
    item = _nugget(session, "extra detail", jira_keys=["PROJ-1"])

    attached = nuggets.attach_nugget(
        session, item.id, task_id=task.id, jira_base_url="https://jira.example/"
    )
    assert attached.id == task.id
    assert attached.title == "existing topic"  # untouched
    nugget = nuggets.get_nugget(session, item.id)
    assert nugget.status == NuggetStatus.ATTACHED and nugget.task_id == task.id
    keys = {(link.system, link.external_key) for link in attached.external_links}
    assert ("notes", f"swd/x.md:1:{item.id}") in keys
    assert ("jira", "PROJ-1") in keys
    jira = next(link for link in attached.external_links if link.system == "jira")
    assert jira.url == "https://jira.example/browse/PROJ-1"

    # attaching a second nugget with the same key does not duplicate the link
    twin = _nugget(session, "same ticket again", path="swd/y.md", jira_keys=["PROJ-1"])
    nuggets.attach_nugget(session, twin.id, task_id=task.id)
    assert [x.external_key for x in attached.external_links if x.system == "jira"] == ["PROJ-1"]


def test_attach_new_task_mirrors_nugget_and_defaults(session: Session) -> None:
    owner = people.create_person(session, "me", is_self=True)
    item = _nugget(session, "new work from notes")
    # override the mirrored description and rely on the self-owner default
    attached = nuggets.attach_nugget(
        session,
        item.id,
        description="a curated description",
        default_assignee_id=nuggets.default_assignee(session, use_self=True),
    )
    assert attached.title == "new work from notes"
    assert attached.description == "a curated description"
    assert attached.assignee_id == owner.id
    assert attached.source == TaskSource.NOTES
    assert nuggets.get_nugget(session, item.id).status == NuggetStatus.ATTACHED


def test_default_assignee_off_when_no_self_or_disabled(session: Session) -> None:
    assert nuggets.default_assignee(session, use_self=False) is None
    assert nuggets.default_assignee(session, use_self=True) is None


def test_attach_to_missing_task_404(session: Session) -> None:
    item = _nugget(session, "orphan")
    with pytest.raises(NotFoundError):
        nuggets.attach_nugget(session, item.id, task_id=999)


def test_attach_does_not_duplicate_citation(session: Session) -> None:
    """If the task already carries this nugget's citation link, keep just one."""
    task = tasks.create_task(session, "topic")
    item = _nugget(session, "detail")
    tasks.link_external(session, task.id, "notes", f"swd/x.md:1:{item.id}")
    nuggets.attach_nugget(session, item.id, task_id=task.id)
    citations = [x for x in task.external_links if x.system == "notes"]
    assert len(citations) == 1


def test_move_and_detach_nugget(session: Session) -> None:
    first = tasks.create_task(session, "first")
    second = tasks.create_task(session, "second")
    item = _nugget(session, "traveler")
    nuggets.attach_nugget(session, item.id, task_id=first.id)
    citation = tasks.find_link(session, "notes", f"swd/x.md:1:{item.id}")
    assert citation is not None and citation.task_id == first.id

    moved = nuggets.move_nugget(session, item.id, second.id)
    assert moved.task_id == second.id and moved.status == NuggetStatus.ATTACHED
    assert citation.task_id == second.id  # citation follows the nugget
    assert [n.id for n in tasks.list_attached_nuggets(session, second.id)] == [item.id]
    assert tasks.list_attached_nuggets(session, first.id) == []

    detached = nuggets.detach_nugget(session, item.id)
    assert detached.task_id is None and detached.status == NuggetStatus.NEW
    assert detached.reviewed_at is None
    assert tasks.find_link(session, "notes", f"swd/x.md:1:{item.id}") is None


def test_detach_never_attached_is_safe(session: Session) -> None:
    """Detach on a nugget without a citation link just returns it to NEW."""
    item = _nugget(session, "plain")
    detached = nuggets.detach_nugget(session, item.id)
    assert detached.status == NuggetStatus.NEW and detached.task_id is None


def test_edit_keeps_fingerprint(session: Session) -> None:
    """Editing the summary must not change dedupe identity on the next scan upsert."""
    item = _nugget(session, "original text")
    nuggets.update_nugget(session, item.id, summary="rewritten text")
    again, created = nuggets.upsert_nugget(
        session, "swd/x.md", 5, "original text", NuggetKind.ACTION
    )
    assert not created and again.id == item.id


def test_find_jira_keys() -> None:
    assert nuggets.find_jira_keys("see DBOARD3-1111 and CHRN-222") == ["DBOARD3-1111", "CHRN-222"]
    assert nuggets.find_jira_keys("no keys here", None) == []
    assert nuggets.find_jira_keys("PROJ-1 PROJ-1") == ["PROJ-1"]


# --- suggest_matches ---


def _match_response(*pairs: tuple[int, int | None]) -> str:
    return json.dumps({"matches": [{"item": item, "task": task} for item, task in pairs]})


def test_suggest_deterministic_duplicate(session: Session) -> None:
    """A repeated item inherits the task its twin was attached to — no LLM call."""
    task = tasks.create_task(session, "the topic")
    attached = _nugget(session, "fix the IX SLA report", path="a/one.md")
    nuggets.attach_nugget(session, attached.id, task_id=task.id)
    duplicate = _nugget(session, "Fix the IX SLA report!", path="b/two.md")  # same words
    llm = FakeLLM()

    summary = suggest_matches(session, llm)
    assert summary.reviewed == 1 and summary.suggested == 1
    assert llm.calls == []  # deterministic pass covered it
    assert nuggets.get_nugget(session, duplicate.id).suggested_task_id == task.id
    assert summary.as_text().startswith("reviewed=1 suggested=1")


def test_suggest_llm_pass_and_no_match(session: Session) -> None:
    topic = tasks.create_task(session, "deploy pipeline work")
    a = _nugget(session, "redo the deploy pipeline", path="a.md", line=1)
    b = _nugget(session, "buy birthday cake", path="b.md", line=2)
    llm = FakeLLM(responses=[_match_response((a.id, topic.id), (b.id, None))])

    summary = suggest_matches(session, llm)
    assert summary.suggested == 1 and summary.no_match == 1
    assert nuggets.get_nugget(session, a.id).suggested_task_id == topic.id
    assert nuggets.get_nugget(session, b.id).suggested_task_id is None
    assert llm.calls and llm.calls[0]["smart"] is False


def test_suggest_ignores_hallucinated_ids_and_bad_json(session: Session) -> None:
    topic = tasks.create_task(session, "real task")
    a = _nugget(session, "something", path="a.md", line=1)
    b = _nugget(session, "else", path="b.md", line=2)
    llm = FakeLLM(
        responses=[
            "not json",
            _match_response((a.id, 9999), (a.id, topic.id), (b.id, topic.id)),
        ]
    )
    summary = suggest_matches(session, llm)
    assert summary.errors == []  # retry succeeded
    # unknown task id → treated as no-match; first match for an item wins
    assert nuggets.get_nugget(session, a.id).suggested_task_id is None
    assert nuggets.get_nugget(session, b.id).suggested_task_id == topic.id


def test_suggest_records_chunk_errors(session: Session) -> None:
    tasks.create_task(session, "a task")
    _nugget(session, "unmatched", path="a.md", line=1)
    llm = FakeLLM(responses=["garbage"])
    summary = suggest_matches(session, llm)
    assert summary.errors and "did not return valid matches" in summary.errors[0]
    assert "errors=1" in summary.as_text()


def test_suggest_fence_stripping_and_id_filter(session: Session) -> None:
    topic = tasks.create_task(session, "the topic")
    a = _nugget(session, "in scope", path="a.md", line=1)
    b = _nugget(session, "out of scope", path="b.md", line=2)
    llm = FakeLLM(responses=["```json\n" + _match_response((a.id, topic.id)) + "\n```"])
    summary = suggest_matches(session, llm, nugget_ids=[a.id])
    assert summary.reviewed == 1
    assert nuggets.get_nugget(session, a.id).suggested_task_id == topic.id
    assert nuggets.get_nugget(session, b.id).suggested_task_id is None


def test_suggest_no_candidates(session: Session) -> None:
    summary = suggest_matches(session, FakeLLM())
    assert summary.reviewed == 0


def test_suggest_deterministic_near_duplicate(session: Session) -> None:
    """A near-identical summary (Jaccard ≥ 0.6) inherits the task — no LLM call."""
    task = tasks.create_task(session, "the topic")
    attached = _nugget(session, "fix the IX SLA report", path="a/one.md")
    nuggets.attach_nugget(session, attached.id, task_id=task.id)
    similar = _nugget(session, "fix the broken IX SLA report", path="b/two.md")
    llm = FakeLLM()

    summary = suggest_matches(session, llm)
    assert summary.reviewed == 1 and summary.suggested == 1
    assert llm.calls == []  # deterministic pass covered it
    assert nuggets.get_nugget(session, similar.id).suggested_task_id == task.id


def test_suggest_deterministic_dissimilar_falls_through(session: Session) -> None:
    """Below the Jaccard threshold the nugget goes to the LLM instead of inheriting."""
    task = tasks.create_task(session, "the topic")
    attached = _nugget(session, "fix the IX SLA report", path="a/one.md")
    nuggets.attach_nugget(session, attached.id, task_id=task.id)
    other = _nugget(session, "report the new hiring plan", path="b/two.md")
    symbols = _nugget(session, "!!! *** !!!", path="c/three.md")  # no word tokens at all
    llm = FakeLLM(responses=[_match_response((other.id, None), (symbols.id, None))])

    summary = suggest_matches(session, llm)
    assert summary.suggested == 0 and summary.no_match == 2
    assert llm.calls  # nothing matched deterministically
    assert nuggets.get_nugget(session, other.id).suggested_task_id is None


def test_suggest_prompt_shows_attached_summaries(session: Session) -> None:
    """Task lines show what already lives on each task (≤80 chars per summary)."""
    topic = tasks.create_task(session, "deploy pipeline work")
    plain = tasks.create_task(session, "untouched topic")
    long_summary = "redo the IX dashboard " + "x" * 100
    for summary_text, path in [("fix the IX SLA report", "a.md"), (long_summary, "b.md")]:
        nugget = _nugget(session, summary_text, path=path)
        nuggets.attach_nugget(session, nugget.id, task_id=topic.id)
    new = _nugget(session, "completely unrelated zzz qqq", path="c.md")
    llm = FakeLLM(responses=[_match_response((new.id, None))])

    suggest_matches(session, llm)
    assert len(llm.calls) == 1
    prompt = str(llm.calls[0]["prompt"])
    expected = (
        f"- #{topic.id} deploy pipeline work — attached: fix the IX SLA report; {long_summary[:80]}"
    )
    assert expected in prompt.splitlines()
    assert "x" * 59 not in prompt  # the long summary was truncated
    assert f"- #{plain.id} untouched topic" in prompt.splitlines()  # no "attached:" suffix


def test_suggest_prompt_caps_attached_summaries(session: Session) -> None:
    """At most three attached summaries per task reach the prompt."""
    topic = tasks.create_task(session, "busy topic")
    for i in range(4):
        nugget = _nugget(session, f"attached detail number {i} zzz", path=f"m/{i}.md")
        nuggets.attach_nugget(session, nugget.id, task_id=topic.id)
    new = _nugget(session, "totally different qqq", path="new.md")
    llm = FakeLLM(responses=[_match_response((new.id, None))])

    suggest_matches(session, llm)
    prompt = str(llm.calls[0]["prompt"])
    topic_line = next(line for line in prompt.splitlines() if line.startswith(f"- #{topic.id}"))
    assert topic_line == (
        f"- #{topic.id} busy topic — attached: attached detail number 0 zzz; "
        "attached detail number 1 zzz; attached detail number 2 zzz"
    )


# --- task_organize ---


def _propose_response(*proposals: dict[str, object]) -> str:
    return json.dumps({"proposals": list(proposals)})


def test_propose_organization_validates(session: Session) -> None:
    survivor = tasks.create_task(session, "survivor")
    loser = tasks.create_task(session, "loser")
    item = _nugget(session, "attached bit")
    nuggets.attach_nugget(session, item.id, task_id=loser.id)
    loose_a = _nugget(session, "loose a", path="a.md", line=1)
    loose_b = _nugget(session, "loose b", path="b.md", line=2)
    llm = FakeLLM(
        responses=[
            _propose_response(
                {"kind": "merge", "into_id": survivor.id, "from_id": loser.id, "reason": "same"},
                {"kind": "merge", "into_id": survivor.id, "from_id": 9999},  # unknown
                {"kind": "split", "task_id": loser.id, "nugget_ids": [item.id], "title": "half"},
                # nugget not attached to the source task → dropped
                {"kind": "split", "task_id": loser.id, "nugget_ids": [9999], "title": "bad"},
                {
                    "kind": "create",
                    "nugget_ids": [loose_a.id, loose_b.id],
                    "title": "cluster",
                },
                {"kind": "create", "nugget_ids": [loose_a.id], "title": "too few"},
                {"kind": "retitle", "task_id": survivor.id, "title": "better"},
            )
        ]
    )
    proposals, tokens = task_organize.propose_organization(session, llm)
    assert tokens == 10
    assert [(p.kind, getattr(p, "title", None)) for p in proposals] == [
        ("merge", None),
        ("split", "half"),
        ("create", "cluster"),
        ("retitle", "better"),
    ]
    assert llm.calls[0]["smart"] is True


def test_propose_organization_bad_json(session: Session) -> None:
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(ValueError, match="did not return valid proposals"):
        task_organize.propose_organization(session, llm)


def test_propose_organization_fence_and_more_drops(session: Session) -> None:
    task = tasks.create_task(session, "task")
    loose = _nugget(session, "loose", path="a.md", line=1)
    llm = FakeLLM(
        responses=[
            "```json\n"
            + _propose_response(
                # merge a task into itself → dropped
                {"kind": "merge", "into_id": task.id, "from_id": task.id},
                # split an unknown task → dropped
                {"kind": "split", "task_id": 9999, "nugget_ids": [loose.id], "title": "x"},
                # create with a nugget that is not NEW → dropped
                {"kind": "create", "nugget_ids": [9999, loose.id], "title": "x"},
                # retitle without a title → dropped
                {"kind": "retitle", "task_id": task.id},
                {"kind": "retitle", "task_id": task.id, "title": "better"},
            )
            + "\n```"
        ]
    )
    proposals, _ = task_organize.propose_organization(session, llm)
    assert [(p.kind, p.title) for p in proposals] == [("retitle", "better")]


def test_apply_merge(session: Session) -> None:
    into = tasks.create_task(session, "into", tags=[])
    loser = tasks.create_task(session, "loser")
    child = tasks.create_task(session, "child", parent_id=loser.id)
    item = _nugget(session, "a nugget", jira_keys=["PROJ-1"])
    nuggets.attach_nugget(session, item.id, task_id=loser.id)
    tasks.add_note(session, "a note", task_id=loser.id)
    tasks.link_external(session, into.id, "jira", "PROJ-1")  # duplicate on survivor → dropped

    merged = task_organize.apply_merge(session, into.id, loser.id)
    assert merged.id == into.id
    assert [n.task_id for n in merged.nuggets] == [into.id]
    assert [n.task_id for n in merged.notes] == [into.id]
    assert tasks.get_task(session, child.id).parent_id == into.id
    # the citation moves with the nugget; the duplicate jira link is dropped
    assert {(x.system, x.external_key) for x in merged.external_links} == {
        ("jira", "PROJ-1"),
        ("notes", f"swd/x.md:1:{item.id}"),
    }
    with pytest.raises(NotFoundError):
        tasks.get_task(session, loser.id)

    with pytest.raises(ValueError, match="into itself"):
        task_organize.apply_merge(session, into.id, into.id)


def test_apply_split(session: Session) -> None:
    source = tasks.create_task(session, "mixed topic")
    keep = _nugget(session, "keep here", path="a.md", line=1)
    move = _nugget(session, "move away", path="b.md", line=2)
    nuggets.attach_nugget(session, keep.id, task_id=source.id)
    nuggets.attach_nugget(session, move.id, task_id=source.id)

    new_task = task_organize.apply_split(session, source.id, [move.id], "separate thing")
    assert new_task.title == "separate thing"
    assert [n.id for n in tasks.list_attached_nuggets(session, new_task.id)] == [move.id]
    assert [n.id for n in tasks.list_attached_nuggets(session, source.id)] == [keep.id]


def test_apply_create(session: Session) -> None:
    a = _nugget(session, "bit a", path="a.md", line=1)
    b = _nugget(session, "bit b", path="b.md", line=2)
    task = task_organize.apply_create(session, "cluster topic", [a.id, b.id])
    assert task.title == "cluster topic"
    assert sorted(n.id for n in tasks.list_attached_nuggets(session, task.id)) == [a.id, b.id]
    assert nuggets.get_nugget(session, a.id).status == NuggetStatus.ATTACHED


def test_apply_proposals_batch(session: Session) -> None:
    into = tasks.create_task(session, "into")
    loser = tasks.create_task(session, "loser")
    item = _nugget(session, "a nugget")
    nuggets.attach_nugget(session, item.id, task_id=loser.id)

    proposals = [
        task_organize.Proposal(kind="merge", into_id=into.id, from_id=loser.id),
        task_organize.Proposal(kind="retitle", task_id=into.id, title="renamed"),
    ]
    errors = task_organize.apply_proposals(session, proposals)
    assert errors == []
    assert tasks.get_task(session, into.id).title == "renamed"
    with pytest.raises(NotFoundError):
        tasks.get_task(session, loser.id)


def test_apply_proposals_reports_errors(session: Session) -> None:
    task = tasks.create_task(session, "solo")
    bad = [task_organize.Proposal(kind="merge", into_id=task.id, from_id=task.id)]
    errors = task_organize.apply_proposals(session, bad)
    assert errors and "into itself" in errors[0]


# --- topic grouping ---


def _topic_group_response(
    task_ids: list[int],
    parent_title: str | None = None,
    existing_parent_id: int | None = None,
    reason: str = "related",
) -> str:
    return json.dumps(
        {
            "task_ids": task_ids,
            "parent_title": parent_title,
            "existing_parent_id": existing_parent_id,
            "reason": reason,
        }
    )


def test_propose_topic_group_validates(session: Session) -> None:
    a = tasks.create_task(session, "redesign website")
    b = tasks.create_task(session, "write homepage copy")
    c = tasks.create_task(session, "fix landing page")
    closed = tasks.create_task(session, "old website")
    tasks.update_task(session, closed.id, status=TaskStatus.DONE)
    llm = FakeLLM(
        responses=[
            _topic_group_response(
                [a.id, b.id, c.id, closed.id, 9999, a.id],  # closed/unknown/dupe dropped
                parent_title="ignored",  # both options set → existing wins
                existing_parent_id=a.id,
            )
        ]
    )
    proposal, tokens = task_organize.propose_topic_group(session, llm, "website")
    assert tokens == 10
    assert proposal is not None
    assert proposal.task_ids == [a.id, b.id, c.id]
    assert proposal.existing_parent_id == a.id
    assert proposal.parent_title is None
    assert llm.calls[0]["smart"] is True


def test_propose_topic_group_needs_two_tasks(session: Session) -> None:
    only = tasks.create_task(session, "lonely website task")
    llm = FakeLLM(responses=[_topic_group_response([only.id, 9999])])
    assert task_organize.propose_topic_group(session, llm, "website")[0] is None


def test_propose_topic_group_caps_at_twenty(session: Session) -> None:
    ids = [tasks.create_task(session, f"topic task {i}").id for i in range(25)]
    llm = FakeLLM(responses=[_topic_group_response(ids, parent_title="all topics")])
    proposal, _ = task_organize.propose_topic_group(session, llm, "topic")
    assert proposal is not None
    assert len(proposal.task_ids) == 20
    assert proposal.existing_parent_id is None
    assert proposal.parent_title == "all topics"


def test_propose_topic_group_bad_json(session: Session) -> None:
    tasks.create_task(session, "a")
    tasks.create_task(session, "b")
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(ValueError, match="did not return valid topic grouping"):
        task_organize.propose_topic_group(session, llm, "x")


def test_propose_topic_group_parent_outside_group(session: Session) -> None:
    a = tasks.create_task(session, "a")
    b = tasks.create_task(session, "b")
    outsider = tasks.create_task(session, "outsider")
    # an existing parent outside the proposed group is dropped, title kept
    llm = FakeLLM(
        responses=[
            _topic_group_response(
                [a.id, b.id], parent_title="new parent", existing_parent_id=outsider.id
            )
        ]
    )
    proposal, _ = task_organize.propose_topic_group(session, llm, "x")
    assert proposal is not None
    assert proposal.existing_parent_id is None
    assert proposal.parent_title == "new parent"


def test_apply_group_new_parent(session: Session) -> None:
    a = tasks.create_task(session, "redesign site")
    b = tasks.create_task(session, "write copy")
    parent = task_organize.apply_group(session, [a.id, b.id], new_parent_title="Website")
    assert parent.title == "Website" and parent.source == TaskSource.MANUAL
    assert tasks.get_task(session, a.id).parent_id == parent.id
    assert tasks.get_task(session, b.id).parent_id == parent.id


def test_apply_group_existing_parent(session: Session) -> None:
    parent = tasks.create_task(session, "website umbrella")
    a = tasks.create_task(session, "part a")
    b = tasks.create_task(session, "part b")
    result = task_organize.apply_group(session, [a.id, b.id], existing_parent_id=parent.id)
    assert result.id == parent.id
    assert tasks.get_task(session, a.id).parent_id == parent.id
    assert tasks.get_task(session, b.id).parent_id == parent.id


def test_apply_group_requires_exactly_one_parent(session: Session) -> None:
    a = tasks.create_task(session, "a")
    b = tasks.create_task(session, "b")
    with pytest.raises(ValueError, match="exactly one"):
        task_organize.apply_group(
            session, [a.id, b.id], new_parent_title="x", existing_parent_id=b.id
        )
    with pytest.raises(ValueError, match="choose a parent"):
        task_organize.apply_group(session, [a.id, b.id])
    with pytest.raises(ValueError, match="needs a title"):
        task_organize.apply_group(session, [a.id, b.id], new_parent_title="  ")


def test_apply_group_rejects_closed_or_missing_children(session: Session) -> None:
    a = tasks.create_task(session, "a")
    closed = tasks.create_task(session, "closed")
    tasks.update_task(session, closed.id, status=TaskStatus.DONE)
    with pytest.raises(ValueError, match="no open tasks selected"):
        task_organize.apply_group(session, [], new_parent_title="x")
    with pytest.raises(ValueError, match="no open tasks selected"):
        task_organize.apply_group(session, [closed.id, 9999], new_parent_title="x")
    # closed/unknown ids are dropped; the open one still groups
    parent = task_organize.apply_group(session, [a.id, closed.id], new_parent_title="x")
    assert tasks.get_task(session, a.id).parent_id == parent.id
    assert tasks.get_task(session, closed.id).parent_id is None
    with pytest.raises(ValueError, match="not an open task"):
        task_organize.apply_group(session, [a.id], existing_parent_id=closed.id)


def test_apply_group_cycle_fails_before_changes(session: Session) -> None:
    child = tasks.create_task(session, "child")
    parent = tasks.create_task(session, "parent", parent_id=child.id)
    other = tasks.create_task(session, "other")
    with pytest.raises(ValueError, match="descendant"):
        task_organize.apply_group(session, [child.id, other.id], existing_parent_id=parent.id)
    assert tasks.get_task(session, child.id).parent_id is None  # nothing reparented
    assert tasks.get_task(session, other.id).parent_id is None

    # the parent itself in the group is also rejected
    with pytest.raises(ValueError, match="part of the group"):
        task_organize.apply_group(session, [child.id, parent.id], existing_parent_id=parent.id)


# --- split parts (free-form split, both modes) ---


def _split_parts_response(mode: str, *parts: dict[str, object]) -> str:
    return json.dumps({"mode": mode, "parts": list(parts)})


def test_propose_split_parts_validates(session: Session) -> None:
    source = tasks.create_task(session, "mixed bag")
    keep_a = _nugget(session, "bit a", path="a.md", line=1)
    keep_b = _nugget(session, "bit b", path="b.md", line=2)
    other = _nugget(session, "elsewhere", path="c.md", line=3)
    nuggets.attach_nugget(session, keep_a.id, task_id=source.id)
    nuggets.attach_nugget(session, keep_b.id, task_id=source.id)
    elsewhere = tasks.create_task(session, "elsewhere")
    nuggets.attach_nugget(session, other.id, task_id=elsewhere.id)
    llm = FakeLLM(
        responses=[
            _split_parts_response(
                "children",
                {"title": "part one", "nugget_ids": [keep_a.id, keep_a.id], "reason": "a"},
                {"title": " part two ", "nugget_ids": [keep_b.id], "reason": "b"},
                {"title": "part one", "nugget_ids": []},  # duplicate title -> dropped
                {"title": "   ", "nugget_ids": []},  # blank title -> dropped
                {"title": "bad attach", "nugget_ids": [other.id]},  # attached elsewhere
                {"title": "bad unknown", "nugget_ids": [9999]},  # unknown id
                {"title": "overlap", "nugget_ids": [keep_a.id]},  # already used
            )
        ]
    )
    proposal, tokens = task_organize.propose_split_parts(session, llm, source.id, "break it up")
    assert tokens == 10
    assert proposal.mode == "children"
    assert [(p.title, p.nugget_ids) for p in proposal.parts] == [
        ("part one", [keep_a.id]),
        ("part two", [keep_b.id]),
    ]
    assert llm.calls[0]["smart"] is True
    prompt = str(llm.calls[0]["prompt"])
    assert f"Source task: #{source.id} [{source.status.value}] mixed bag" in prompt
    assert "Instructions: break it up" in prompt


def test_propose_split_parts_caps_at_eight(session: Session) -> None:
    source = tasks.create_task(session, "big task")
    parts = [{"title": f"part {i}", "nugget_ids": []} for i in range(10)]
    llm = FakeLLM(responses=[_split_parts_response("siblings", *parts)])
    proposal, _ = task_organize.propose_split_parts(session, llm, source.id, "split")
    assert proposal.mode == "siblings"
    assert [p.title for p in proposal.parts] == [f"part {i}" for i in range(8)]
    prompt = str(llm.calls[0]["prompt"])
    assert "Attached nuggets:\n- (none)" in prompt


def test_propose_split_parts_bad_json(session: Session) -> None:
    source = tasks.create_task(session, "a task")
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(ValueError, match="did not return valid split parts"):
        task_organize.propose_split_parts(session, llm, source.id, "split it")


def test_propose_split_parts_missing_task(session: Session) -> None:
    with pytest.raises(NotFoundError):
        task_organize.propose_split_parts(session, FakeLLM(), 9999, "split it")


def test_apply_split_parts_children(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    source = tasks.create_task(
        session, "mixed bag", priority=TaskPriority.HIGH, assignee_id=anna.id
    )
    keep = _nugget(session, "stays", path="a.md", line=1)
    move_a = _nugget(session, "to part a", path="b.md", line=2)
    move_b = _nugget(session, "to part b", path="c.md", line=3)
    for item in (keep, move_a, move_b):
        nuggets.attach_nugget(session, item.id, task_id=source.id)

    created = task_organize.apply_split_parts(
        session,
        source.id,
        [
            task_organize.SplitPart(title="part a", nugget_ids=[move_a.id]),
            task_organize.SplitPart(title="part b", nugget_ids=[move_b.id]),
        ],
        "children",
    )
    assert [t.title for t in created] == ["part a", "part b"]
    for new_task in created:
        assert new_task.parent_id == source.id
        assert new_task.priority == TaskPriority.HIGH
        assert new_task.source == source.source
        assert new_task.assignee_id == anna.id
    assert [n.id for n in tasks.list_attached_nuggets(session, created[0].id)] == [move_a.id]
    assert [n.id for n in tasks.list_attached_nuggets(session, created[1].id)] == [move_b.id]
    assert [n.id for n in tasks.list_attached_nuggets(session, source.id)] == [keep.id]
    # the citation link follows the moved nugget
    citation = tasks.find_link(session, "notes", f"b.md:2:{move_a.id}")
    assert citation is not None and citation.task_id == created[0].id


def test_apply_split_parts_siblings(session: Session) -> None:
    parent = tasks.create_task(session, "umbrella")
    source = tasks.create_task(session, "mixed bag", parent_id=parent.id)
    move = _nugget(session, "to sibling", path="a.md", line=1)
    nuggets.attach_nugget(session, move.id, task_id=source.id)

    created = task_organize.apply_split_parts(
        session,
        source.id,
        [task_organize.SplitPart(title="sib", nugget_ids=[move.id])],
        "siblings",
    )
    assert [t.parent_id for t in created] == [parent.id]
    assert created[0].priority == source.priority and created[0].source == source.source
    assert [n.id for n in tasks.list_attached_nuggets(session, created[0].id)] == [move.id]
    assert tasks.list_attached_nuggets(session, source.id) == []


def test_apply_split_parts_rejects_unknown_mode(session: Session) -> None:
    source = tasks.create_task(session, "source")
    with pytest.raises(ValueError, match="unknown split mode"):
        task_organize.apply_split_parts(session, source.id, [], "sideways")  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        task_organize.apply_split_parts(session, 9999, [], "children")


# --- related grouping (new task -> clarifying questions -> grouping) ---


def _related_questions_response(*questions: str) -> str:
    return json.dumps({"questions": list(questions)})


def test_propose_related_questions_path(session: Session) -> None:
    new = tasks.create_task(session, "overhaul the ix sla report")
    llm = FakeLLM(
        responses=[
            _related_questions_response(
                "  Is this about the public dashboard?  ",
                "Does it block OAM acceptance?",
                "Is this about the public dashboard?",  # duplicate -> dropped
                "   ",  # blank -> dropped
                "Which quarter does it target?",
                "Who consumes it?",  # beyond the cap of 3 -> dropped
            )
        ]
    )
    result, tokens = task_organize.propose_related(session, llm, new.id)
    assert tokens == 10
    assert isinstance(result, task_organize.RelatedQuestions)
    assert result.questions == [
        "Is this about the public dashboard?",
        "Does it block OAM acceptance?",
        "Which quarter does it target?",
    ]
    assert llm.calls[0]["smart"] is True
    assert f"- #{new.id} [{new.status.value}] overhaul the ix sla report" in str(
        llm.calls[0]["prompt"]
    )


def test_propose_related_qa_path_returns_topic_group(session: Session) -> None:
    new = tasks.create_task(session, "ix sla report overhaul")
    a = tasks.create_task(session, "redesign sla dashboard")
    b = tasks.create_task(session, "fix sla data pipeline")
    closed = tasks.create_task(session, "old sla thing")
    tasks.update_task(session, closed.id, status=TaskStatus.DONE)
    llm = FakeLLM(
        responses=[
            _topic_group_response(
                [new.id, a.id, b.id, closed.id, 9999, a.id],  # closed/unknown/dupe dropped
                parent_title="ignored",  # both options set -> existing wins
                existing_parent_id=new.id,
                reason="same report work",
            )
        ]
    )
    result, tokens = task_organize.propose_related(
        session, llm, new.id, qa=[("Public or internal dashboard?", "Public")]
    )
    assert tokens == 10
    assert isinstance(result, task_organize.TopicGroupProposal)
    assert result.task_ids == [new.id, a.id, b.id]
    assert result.existing_parent_id == new.id
    assert result.parent_title is None
    assert result.reason == "same report work"
    assert llm.calls[0]["smart"] is True
    prompt = str(llm.calls[0]["prompt"])
    assert "Q: Public or internal dashboard?\nA: Public" in prompt
    assert f"- #{a.id} [{a.status.value}] redesign sla dashboard" in prompt


def test_propose_related_qa_prompt_forbids_questions(session: Session) -> None:
    new = tasks.create_task(session, "new thing")
    other = tasks.create_task(session, "other thing")
    llm = FakeLLM(responses=[_topic_group_response([new.id, other.id], parent_title="g")])
    task_organize.propose_related(session, llm, new.id, qa=[("Scope?", "all of it")])
    assert "Do not ask questions" in str(llm.calls[0]["prompt"])
    assert "Do not ask any questions" in str(llm.calls[0]["system"])


def test_propose_related_qa_path_needs_two_tasks(session: Session) -> None:
    new = tasks.create_task(session, "lonely new task")
    llm = FakeLLM(responses=[_topic_group_response([new.id, 9999])])
    result, _ = task_organize.propose_related(session, llm, new.id, qa=[("q", "a")])
    assert result is None


def test_propose_related_bad_json(session: Session) -> None:
    new = tasks.create_task(session, "a task")
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(ValueError, match="did not return valid related questions"):
        task_organize.propose_related(session, llm, new.id)
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(ValueError, match="did not return valid related grouping"):
        task_organize.propose_related(session, llm, new.id, qa=[("q", "a")])


def test_propose_related_missing_task(session: Session) -> None:
    with pytest.raises(NotFoundError):
        task_organize.propose_related(session, FakeLLM(), 9999)
