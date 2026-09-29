"""Guards the golden corpus itself, so a missing session cannot pass silently.

The corpus is an explicit list of names. Every listed session must exist with
all of its snapshots, every session file on disk must be listed, and every
snapshot must belong to a listed session.
"""

from __future__ import annotations

import pytest

from .conftest import SNAPSHOT_DIR
from .corpus import CORPUS, SESSIONS_DIR, SNAPSHOT_KINDS, SYNTHETIC_SESSIONS


def _session_name(snapshot_name: str) -> str | None:
    """Strip an exact ``.<kind>.json`` suffix; ``None`` for an unknown kind."""
    for kind in SNAPSHOT_KINDS:
        suffix = f".{kind}.json"
        if snapshot_name.endswith(suffix):
            return snapshot_name[: -len(suffix)]
    return None


@pytest.mark.req("TER-ANL-000")
def test_every_session_exists_and_has_snapshots() -> None:
    for name, path in CORPUS.items():
        assert path.is_file(), f"golden session {name} is missing: {path}"
        for kind in SNAPSHOT_KINDS:
            snapshot = SNAPSHOT_DIR / f"{name}.{kind}.json"
            assert snapshot.is_file(), f"{name} has no {kind} snapshot"


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
