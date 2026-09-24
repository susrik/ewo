"""Read-only notes reader: enumeration, dating, context chain, windowing."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ewo.core.notes import (
    NotesReader,
    format_numbered,
    in_window_lines,
    note_date,
    numbered_lines,
)


def test_iter_notes_skips_hidden_context_and_excluded(notes_root: Path) -> None:
    reader = NotesReader(notes_root, exclude=("eurohpc/ignored.md", "swd/people/former"))
    paths = [n.path for n in reader.iter_notes()]
    assert "eurohpc/2026-09-03-oam.md" in paths
    assert "swd/people/james_l/old.md" in paths
    assert "TODO.md" in paths
    assert not any("AGENTS.md" in p for p in paths)
    assert not any(p.startswith(".obsidian") for p in paths)
    assert "eurohpc/ignored.md" not in paths
    assert not any(p.startswith("swd/people/former") for p in paths)


def test_note_date_precedence(notes_root: Path) -> None:
    reader = NotesReader(notes_root)
    by_path = {n.path: n for n in reader.iter_notes()}
    assert by_path["eurohpc/2026-09-03-oam.md"].date == date(2026, 9, 3)  # frontmatter
    assert by_path["swd/people/james_l/2026-09-14-james-121.md"].date == date(2026, 9, 14)
    assert by_path["TODO.md"].date == by_path["TODO.md"].modified.date()  # mtime fallback

    mtime = datetime(2026, 1, 1)
    assert note_date("x/2026-13-45-bad.md", "", mtime) == mtime.date()  # invalid filename date
    assert note_date("x/2026-9-8-short.md", "", mtime) == date(2026, 9, 8)


def test_changed_since_and_window(notes_root: Path) -> None:
    reader = NotesReader(notes_root)
    old = notes_root / "TODO.md"
    stamp = time.mktime(datetime(2026, 1, 1).timetuple())
    os.utime(old, (stamp, stamp))

    # full window: dated or modified inside the window
    window = reader.changed_since(None, window_start=date(2026, 9, 1))
    paths = {n.path for n in window}
    assert "eurohpc/2026-09-03-oam.md" in paths
    assert "TODO.md" not in paths

    # incremental: only mtime after `since`
    assert reader.changed_since(datetime.now() + timedelta(days=1), date(2000, 1, 1)) == []
    recent = reader.changed_since(datetime(2026, 6, 1), date(2000, 1, 1))
    assert "TODO.md" not in {n.path for n in recent}
    assert "eurohpc/2026-09-03-oam.md" in {n.path for n in recent}


def test_read_and_escape(notes_root: Path) -> None:
    reader = NotesReader(notes_root)
    assert "ACSA" in reader.read("eurohpc/2026-09-03-oam.md")
    with pytest.raises(ValueError, match="escapes"):
        reader.read("../outside.md")


def test_context_chain(notes_root: Path) -> None:
    reader = NotesReader(notes_root)
    chain = reader.context_chain("swd/people/james_l/2026-09-14-james-121.md")
    assert chain.index("# Context from: (notes root)") < chain.index("# Context from: swd/people")
    assert chain.index("swd/people\n") < chain.index("swd/people/james_l")
    assert "James L: dashboards." in chain
    assert (
        reader.context_chain("TODO.md")
        == "# Context from: (notes root)\n\n# Root context\nErik is the manager."
    )


def test_people_dirs(notes_root: Path) -> None:
    reader = NotesReader(notes_root)
    assert reader.people_dirs("swd/people") == ["swd/people/james_l", "swd/people/james_s"]
    assert reader.people_dirs("nope") == []


def test_in_window_lines() -> None:
    text = (
        "# James\n\n## 2026-09-01\n\n- recent\n\n## 2025-01-01\n\n- ancient\n"
        "## 2027-9-12\n- typo-year\n"
    )
    lines = numbered_lines(text)
    kept = in_window_lines(lines, date(2026, 8, 1))
    assert [t for _, t in kept] == [
        "## 2026-09-01",
        "",
        "- recent",
        "",
        "## 2027-9-12",
        "- typo-year",
    ]
    assert kept[0][0] == 3  # original line numbers preserved
    assert format_numbered(kept[:1]) == "3: ## 2026-09-01"

    plain = numbered_lines("- no headings\n- at all\n")
    assert in_window_lines(plain, date(2026, 8, 1)) == plain
    assert in_window_lines(numbered_lines("## 2025-01-01\n- old\n"), date(2026, 8, 1)) == []
    assert in_window_lines(numbered_lines("## 2026-99-01\n- bad\n"), date(2026, 8, 1)) == []
