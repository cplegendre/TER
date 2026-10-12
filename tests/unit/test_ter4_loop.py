"""L4 Advisory: the control loop is TER's central flow (TER-INT-013).

Each turn runs Observe, Analyse, Decide, Intervene, Measure and Learn, in
that order, as named stages. Shadow runs only up to Decide. Measure and
Learn never cost advice already decided, and Learn never changes a policy.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from ter import bootstrap
from ter.adapters.driven.in_memory import (
    FixedClock,
    InMemoryEventLog,
    InMemoryInterventionChannel,
    InMemoryInterventionLedger,
)
from ter.application import RecordEvent
from ter.application.loop import ControlLoop
from ter.domain.loop import ORDER, Stage
from ter.domain.policy_set import PolicySet, UnapprovedPolicyError

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "docs" / "examples" / "policies" / "l4-advisory.json"
HOOKS = REPO / "tests" / "fixtures" / "hooks"
AT = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)


def draft() -> PolicySet:
    return PolicySet.from_mapping(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def approved() -> PolicySet:
    return draft().approve("Test", date(2026, 10, 12))


def record(log: InMemoryEventLog, calls: int, start: int = 0) -> str:
    """Reads repeated between shell calls, as the hook records them."""
    from ter.adapters.driving.claude_hooks import handle_hook

    names = ("post_tool_use_bash", "post_tool_use_read")
    templates = [json.loads((HOOKS / f"{n}.json").read_text()) for n in names]
    recorder = RecordEvent(bootstrap.make_tokenizer("regex"), log)
    for n in range(start, start + calls):
        handle_hook(dict(templates[n % 2], tool_use_id=f"t{n}"), recorder)
    return str(templates[0]["session_id"])


def ingest() -> Any:
    return bootstrap.make_ingest("regex")


def loop(
    ledger: InMemoryInterventionLedger,
    channel: InMemoryInterventionChannel | None,
    policies: PolicySet | None = None,
) -> ControlLoop:
    return ControlLoop(policies or approved(), ledger, FixedClock(AT), channel)


@pytest.mark.req("TER-INT-013")
def test_the_stages_are_named_in_order() -> None:
    assert [s.value for s in ORDER] == [
        "observe",
        "analyse",
        "decide",
        "intervene",
        "measure",
        "learn",
    ]


@pytest.mark.req("TER-INT-013")
def test_a_live_turn_runs_every_stage_in_order() -> None:
    log, ledger = InMemoryEventLog(), InMemoryInterventionLedger()
    session = record(log, 8)
    first = loop(ledger, InMemoryInterventionChannel()).turn(session, log, ingest)
    # Nothing to measure yet, so nothing to learn from.
    assert first.stages == ORDER[:5] and first.learned == ()
    assert first.tool_events == 8 and len(first.delivered) == 1

    record(log, 12, start=8)  # past the advice's window
    channel = InMemoryInterventionChannel()
    later = loop(ledger, channel).turn(session, log, ingest)
    assert later.stages == ORDER
    [observed] = later.observed
    assert observed.id == first.delivered[0].record.id
    assert [p.policy for p in later.learned] == ["reuse-reads"]
    assert later.errors == ()


@pytest.mark.req("TER-INT-013", "TER-INT-017")
def test_shadow_stops_after_decide_and_delivers_nothing() -> None:
    log, ledger = InMemoryEventLog(), InMemoryInterventionLedger()
    session = record(log, 20)
    turn = loop(ledger, None).turn(session, log, ingest)
    assert turn.stages == (Stage.OBSERVE, Stage.ANALYSE, Stage.DECIDE)
    assert turn.delivered == () and turn.observed == () and turn.learned == ()
    advised = [r for r in ledger.entries(session) if r.delivered]
    assert advised and all(r.delivered_at is None for r in advised)


@pytest.mark.req("TER-INT-013", "TER-INT-015")
def test_an_unapproved_file_stops_the_loop_before_it_observes() -> None:
    class Unread(InMemoryEventLog):
        def events(self, session_id: str) -> Any:
            raise AssertionError("observed under an unapproved file")

    ledger = InMemoryInterventionLedger()
    with pytest.raises(UnapprovedPolicyError):
        loop(ledger, InMemoryInterventionChannel(), draft()).turn("s", Unread(), ingest)
    assert ledger.sessions() == ()


@pytest.mark.req("TER-INT-013")
def test_a_failed_measure_keeps_the_advice_already_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ter.application import loop as loop_module

    def broken(*args: Any) -> Any:
        raise OSError("ledger unreadable")

    monkeypatch.setattr(loop_module, "follow_up_session", broken)
    log, channel = InMemoryEventLog(), InMemoryInterventionChannel()
    session = record(log, 8)
    turn = loop(InMemoryInterventionLedger(), channel).turn(session, log, ingest)
    assert len(channel.interventions) == 1
    assert turn.stages == ORDER[:5] and turn.learned == ()
    assert turn.errors == ((Stage.MEASURE, "OSError: ledger unreadable"),)


@pytest.mark.req("TER-INT-013", "TER-INT-015")
def test_learn_reports_and_never_changes_a_policy() -> None:
    log, ledger = InMemoryEventLog(), InMemoryInterventionLedger()
    policies = approved()
    before = policies.as_dict()
    session = record(log, 8)
    loop(ledger, InMemoryInterventionChannel(), policies).turn(session, log, ingest)
    record(log, 12, start=8)
    turn = loop(ledger, InMemoryInterventionChannel(), policies).turn(
        session, log, ingest
    )
    assert turn.learned and Stage.LEARN in turn.stages
    assert policies.as_dict() == before
    policies.require_approved()  # still the file a person approved


@pytest.mark.req("TER-INT-013")
def test_the_architecture_doc_puts_the_loop_at_the_centre() -> None:
    text = (REPO / "docs" / "ter4" / "architecture.md").read_text(encoding="utf-8")
    section = text.split("## The control loop", 1)[1].split("\n## ", 1)[0]
    assert "central control flow" in section
    named = [s.value.capitalize() for s in ORDER]
    positions = [section.index(f"**{name}**") for name in named]
    assert positions == sorted(positions)
