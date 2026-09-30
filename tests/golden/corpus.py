"""The golden corpus: sessions whose analysis is frozen by snapshot tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parent
SESSIONS_DIR = GOLDEN_DIR / "sessions"

#: Synthetic sessions under ``sessions/``. Listed explicitly rather than
#: globbed, so a lost file fails the integrity tests instead of silently
#: dropping its golden tests.
SYNTHETIC_SESSIONS: tuple[str, ...] = (
    "duplicate_exploration",
    "handoff_fetch",
    "intent_shift",
    "rework_loop",
)

#: Every session whose behaviour is frozen. Names are snapshot stems.
CORPUS: dict[str, Path] = {
    "example_session": REPO_ROOT / "sample_sessions" / "example_session.jsonl",
    "fixture_session": REPO_ROOT / "tests" / "fixtures" / "sample_session.jsonl",
    **{name: SESSIONS_DIR / f"{name}.jsonl" for name in SYNTHETIC_SESSIONS},
}

#: Snapshot kinds every session must have, as ``<name>.<kind>.json``.
SNAPSHOT_KINDS: tuple[str, ...] = ("default", "fine", "events")
