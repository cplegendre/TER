"""Guards the golden corpus itself, so a missing session cannot pass silently.

The corpus is an explicit list of names. Every listed session must exist with
all of its snapshots, every session file on disk must be listed, and every
snapshot must belong to a listed session. Rendered reports under
``snapshots/report/`` follow the same rules: every session has its report
files, and every file there belongs to a listed session.
"""

from __future__ import annotations

import pytest

from .conftest import SNAPSHOT_DIR
from .corpus import (
    CHART_SESSIONS,
    CORPUS,
    REPORT_KINDS,
    SESSIONS_DIR,
    SNAPSHOT_KINDS,
    SYNTHETIC_SESSIONS,
)

REPORT_DIR = SNAPSHOT_DIR / "report"


def _session_name(snapshot_name: str) -> str | None:
    """Strip an exact ``.<kind>.json`` suffix; ``None`` for an unknown kind."""
    for kind in SNAPSHOT_KINDS:
        suffix = f".{kind}.json"
        if snapshot_name.endswith(suffix):
            return snapshot_name[: -len(suffix)]
    return None


def _report_owner(relative: str) -> str | None:
    """The session a file under ``report/`` belongs to; ``None`` if unknown.

    Accepts ``<name>.<kind>`` for a report kind, and ``<name>/<chart>.svg``
    for a chart session.
    """
    parts = relative.split("/")
    if len(parts) == 1:
        # Longest kind first, so ``x.a3.html`` is an A3 report of ``x``.
        for kind in sorted(REPORT_KINDS, key=len, reverse=True):
            suffix = f".{kind}"
            if parts[0].endswith(suffix):
                return parts[0][: -len(suffix)]
        return None
    if len(parts) == 2 and parts[1].endswith(".svg") and parts[0] in CHART_SESSIONS:
        return parts[0]
    return None


@pytest.mark.req("TER-ANL-000")
def test_every_session_exists_and_has_snapshots() -> None:
    for name, path in CORPUS.items():
        assert path.is_file(), f"golden session {name} is missing: {path}"
        for kind in SNAPSHOT_KINDS:
            snapshot = SNAPSHOT_DIR / f"{name}.{kind}.json"
            assert snapshot.is_file(), f"{name} has no {kind} snapshot"
        for kind in REPORT_KINDS:
            report = REPORT_DIR / f"{name}.{kind}"
            assert report.is_file(), f"{name} has no report/{name}.{kind} snapshot"
    for name in CHART_SESSIONS:
        assert name in CORPUS, f"chart session {name} is not in the corpus"
        assert any((REPORT_DIR / name).glob("*.svg")), f"{name} has no chart snapshots"


@pytest.mark.req("TER-ANL-000")
def test_every_session_file_is_listed() -> None:
    on_disk = {p.stem for p in SESSIONS_DIR.glob("*.jsonl")}
    unlisted = sorted(on_disk - set(SYNTHETIC_SESSIONS))
    assert not unlisted, f"session files not in SYNTHETIC_SESSIONS: {unlisted}"


@pytest.mark.req("TER-ANL-000")
def test_every_snapshot_belongs_to_a_session() -> None:
    orphans = sorted(
        p.name
        for p in SNAPSHOT_DIR.glob("*.json")
        if _session_name(p.name) not in CORPUS
    )
    assert not orphans, f"golden snapshots without a listed session: {orphans}"


@pytest.mark.req("TER-ANL-000")
def test_every_report_file_belongs_to_a_session() -> None:
    orphans = sorted(
        relative
        for relative in (
            p.relative_to(REPORT_DIR).as_posix()
            for p in REPORT_DIR.rglob("*")
            if p.is_file()
        )
        if _report_owner(relative) not in CORPUS
    )
    assert not orphans, f"report snapshots without a listed session: {orphans}"


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ("foo.bar.default.json", "foo.bar"),
        ("example_session.events.json", "example_session"),
        ("example_session.old.json", None),
    ],
)
def test_session_name_strips_only_known_suffixes(
    snapshot: str, expected: str | None
) -> None:
    assert _session_name(snapshot) == expected


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("example_session.html", "example_session"),
        ("example_session.a3.html", "example_session"),
        ("example_session/composition.svg", "example_session"),
        ("example_session/notes.txt", None),
        ("rework_loop/composition.svg", None),
        ("example_session.pdf", None),
        ("a/b/c.svg", None),
    ],
)
def test_report_owner_accepts_only_known_layouts(
    relative: str, expected: str | None
) -> None:
    assert _report_owner(relative) == expected
