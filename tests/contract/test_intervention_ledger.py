"""Contract suite for the ``InterventionLedger`` port (TER-INT-009).

The JSONL ledger and the in-memory fake run the same assertions, so the fake
cannot drift from the obligations the real adapter meets.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ter.adapters.driven.in_memory import InMemoryInterventionLedger
from ter.adapters.driven.intervention_ledger import JsonlInterventionLedger
from ter.domain.intervention import (
    Advice,
    AdviceKind,
    Eligibility,
    Policy,
    Signal,
    decide,
)
from ter.domain.ledger import LedgerError, LedgerRecord, Observation, Response
from ter.ports import InterventionLedger

AT = datetime(2026, 10, 12, 17, 30, tzinfo=UTC)

POLICY = Policy(
    "reuse-reads",
    frozenset({"repeated_exploration"}),
    Advice(AdviceKind.WARNING, "Reuse the earlier read."),
    min_evidence=2,
    cooldown_tool_events=5,
)


def signal(n: int, *, confidence: float = 0.85, session: str = "s1") -> Signal:
    return Signal(
        id=f"repeated_exploration:{session}-e{n}",
        detector="repeated_exploration",
        waste="motion",
        kind="waste",
        confidence=confidence,
        title="Re-read src/a.py",
        subject="src/a.py",
        evidence=(f"{session}-e{n - 1}", f"{session}-e{n}"),
        rule="0.85 when the same read returned identical output",
        position=n,
    )


def records(session: str = "s1") -> tuple[LedgerRecord, ...]:
    """Advised at 2, cooldown at 4, advised at 8, low confidence at 9."""
    signals = [
        signal(2, session=session),
        signal(4, session=session),
        signal(8, session=session),
        signal(9, confidence=0.6, session=session),
    ]
    verdicts = decide(
        signals, [POLICY], Eligibility(frozenset({"repeated_exploration"}), "judged")
    )
    return tuple(LedgerRecord.from_verdict(v, session, AT) for v in verdicts)


def observation(response: Response = Response.ACCEPTED) -> Observation:
    return Observation(
        response,
        AT,
        tool_events=10,
        before={"rework_ratio": 0.4},
        after={"rework_ratio": 0.1},
    )


@pytest.fixture(
    params=[
        lambda p: JsonlInterventionLedger(p / "ledger"),
        lambda p: InMemoryInterventionLedger(),
    ],
    ids=["jsonl", "in-memory"],
)
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> InterventionLedger:
    factory: Callable[[Path], InterventionLedger] = request.param
    return factory(tmp_path)


@pytest.mark.req("TER-INT-009")
def test_satisfies_the_port_protocol(ledger: InterventionLedger) -> None:
    assert isinstance(ledger, InterventionLedger) and ledger.name


@pytest.mark.req("TER-INT-009")
def test_entries_come_back_in_record_order_field_for_field(
    ledger: InterventionLedger,
) -> None:
    written = records()
    for entry in written:
        ledger.record(entry)
    assert ledger.entries("s1") == written
    assert [e.outcome.value for e in written] == [
        "advised",
        "cooldown",
        "advised",
        "low_confidence",
    ]


@pytest.mark.req("TER-INT-009")
def test_each_record_holds_signal_evidence_action_and_outcome(
    ledger: InterventionLedger,
) -> None:
    advised = records()[0]
    ledger.record(advised)
    ledger.observe("s1", advised.id, observation())
    [entry] = ledger.entries("s1")
    assert entry.signal_id == "repeated_exploration:s1-e2"
    assert entry.detector == "repeated_exploration" and entry.rule
    assert entry.evidence == ("s1-e1", "s1-e2")
    assert entry.action is not None and entry.action.value == "advise"
    assert entry.advice == "Reuse the earlier read."
    assert entry.observation == observation()


@pytest.mark.req("TER-INT-009")
def test_recording_a_record_again_keeps_the_first(ledger: InterventionLedger) -> None:
    first = records()[0]
    ledger.record(first)
    ledger.record(first)
    assert ledger.entries("s1") == (first,)


@pytest.mark.req("TER-INT-009")
def test_an_outcome_is_attached_once(ledger: InterventionLedger) -> None:
    advised = records()[0]
    ledger.record(advised)
    ledger.observe("s1", advised.id, observation())
    with pytest.raises(LedgerError, match="already has its outcome"):
        ledger.observe("s1", advised.id, observation(Response.REJECTED))
    assert ledger.entries("s1")[0].observation == observation()


@pytest.mark.req("TER-INT-009")
def test_a_suppressed_decision_has_no_outcome_to_observe(
    ledger: InterventionLedger,
) -> None:
    suppressed = records()[1]
    ledger.record(suppressed)
    with pytest.raises(LedgerError, match="no intervention to observe"):
        ledger.observe("s1", suppressed.id, observation())


def test_an_unknown_record_cannot_be_observed(ledger: InterventionLedger) -> None:
    with pytest.raises(LedgerError, match="no ledger record"):
        ledger.observe("s1", "int-missing", observation())


def test_a_record_is_written_before_its_outcome(ledger: InterventionLedger) -> None:
    observed = records()[0].observed(observation())
    with pytest.raises(LedgerError, match="then observe"):
        ledger.record(observed)


def test_sessions_never_leak(ledger: InterventionLedger) -> None:
    for entry in records("s2") + records("s1"):
        ledger.record(entry)
    assert ledger.sessions() == ("s1", "s2")
    assert all(e.session_id == "s1" for e in ledger.entries("s1"))
    assert ledger.entries("unknown") == ()


# --- the JSONL ledger ------------------------------------------------------------


def test_jsonl_lines_hold_no_session_content(tmp_path: Path) -> None:
    ledger = JsonlInterventionLedger(tmp_path)
    advised = records()[0]
    ledger.record(advised)
    ledger.observe("s1", advised.id, observation())
    text = ledger.path_for("s1").read_text(encoding="utf-8")
    # The finding's title and subject (a path) are not written.
    assert "src/a.py" not in text and "Re-read" not in text
    kinds = [json.loads(line)["type"] for line in text.splitlines()]
    assert kinds == ["record", "observation"]


def test_jsonl_skips_a_torn_line_and_starts_the_next_on_its_own(
    tmp_path: Path,
) -> None:
    ledger = JsonlInterventionLedger(tmp_path)
    first, second = records()[:2]
    ledger.record(first)
    with open(ledger.path_for("s1"), "a", encoding="utf-8") as handle:
        handle.write('{"schema": "ter.intervention-ledger/1", "type": "rec')
    ledger.record(second)
    assert ledger.entries("s1") == (first, second)
    assert ledger.skipped == 1


def test_jsonl_hashes_an_unsafe_session_id_into_a_file_name(tmp_path: Path) -> None:
    ledger = JsonlInterventionLedger(tmp_path)
    path = ledger.path_for("../../etc/passwd")
    assert path.parent == tmp_path and path.name.startswith("sid-")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_jsonl_files_are_private(tmp_path: Path) -> None:
    ledger = JsonlInterventionLedger(tmp_path / "ledger")
    ledger.record(records()[0])
    assert stat.S_IMODE((tmp_path / "ledger").stat().st_mode) == 0o700
    assert stat.S_IMODE(ledger.path_for("s1").stat().st_mode) == 0o600
