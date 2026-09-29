"""Guards the golden corpus itself, so a missing session cannot pass silently.

The corpus is discovered from files on disk. If a session file is lost (for
example because an ignore rule kept it out of version control), its golden
tests would simply not be generated. Every snapshot must therefore have its
session, and every session its snapshots.
"""

from __future__ import annotations

import pytest

from .conftest import SNAPSHOT_DIR
from .corpus import CORPUS


@pytest.mark.req("TER-ANL-000")
def test_every_snapshot_has_its_session() -> None:
    stems = {p.name.split(".")[0] for p in SNAPSHOT_DIR.glob("*.json")}
    missing = sorted(stems - set(CORPUS))
    assert not missing, f"golden snapshots without a session file: {missing}"


@pytest.mark.req("TER-ANL-000")
def test_every_session_exists_and_has_snapshots() -> None:
    for name, path in CORPUS.items():
        assert path.is_file(), path
        for suffix in ("default", "fine", "events"):
            assert (SNAPSHOT_DIR / f"{name}.{suffix}.json").is_file(), (name, suffix)
