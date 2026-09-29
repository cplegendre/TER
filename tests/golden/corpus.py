"""The golden corpus: sessions whose analysis is frozen by snapshot tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parent

#: Every session whose behaviour is frozen. Names are snapshot stems.
CORPUS: dict[str, Path] = {
    "example_session": REPO_ROOT / "sample_sessions" / "example_session.jsonl",
    "fixture_session": REPO_ROOT / "tests" / "fixtures" / "sample_session.jsonl",
    **{p.stem: p for p in sorted((GOLDEN_DIR / "sessions").glob("*.jsonl"))},
}
