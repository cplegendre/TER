"""L4 Advisory: intervention ledger records (TER-INT-009, points 175 to 178).

One record per engine decision holds the triggering signal, its evidence,
the action taken and, once observed, the outcome. The port's obligations are
in ``tests/contract/test_intervention_ledger.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ter import bootstrap
from ter.adapters.driven.intervention_ledger import JsonlInterventionLedger
from ter.domain.intervention import (
    Action,
    Advice,
    AdviceKind,
    Eligibility,
    InterventionEngine,
    Outcome,
    Policy,
    Signal,
)
from ter.domain.ledger import (
    LedgerError,
    LedgerRecord,
    Observation,
    Response,
    record_id,
)
from ter.ports import InterventionLedger

AT = datetime(2026, 10, 12, 17, 30, tzinfo=UTC)
ELIGIBLE = Eligibility(frozenset({"fragmented_edits"}), "judge-l4")


def policy(**kw: Any) -> Policy:
    return Policy(
        "batch-edits",
        frozenset({"fragmented_edits"}),
        Advice(AdviceKind.RECOMMENDATION, "Make the change in one edit."),
        **kw,
    )


def signal(
    n: int = 3, *, confidence: float = 0.8, evidence: tuple[str, ...] = ("e1", "e3")
) -> Signal:
    return Signal(
        id=f"fragmented_edits:e{n}",
        detector="fragmented_edits",
        waste="motion",
        kind="waste",
        confidence=confidence,
        title="Edited src/b.py in 3 round trips",
        subject="src/b.py",
        evidence=evidence,
        rule="0.80 when one file is edited in 3 or more round trips",
        position=n,
    )


def advised() -> LedgerRecord:
    [verdict] = InterventionEngine((policy(),), ELIGIBLE).apply(signal())
    return LedgerRecord.from_verdict(verdict, "s1", AT)


def observation(**kw: Any) -> Observation:
    base: dict[str, Any] = {
        "response": Response.ACCEPTED,
        "observed_at": AT,
        "tool_events": 10,
        "before": {"rework_ratio": 0.5},
        "after": {"rework_ratio": 0.25},
    }
    base.update(kw)
    return Observation(**base)


@pytest.mark.req("TER-INT-009")
def test_an_advised_verdict_records_signal_evidence_and_action() -> None:
    entry = advised()
    assert entry.delivered and entry.action is Action.ADVISE
    assert (entry.signal_id, entry.detector, entry.waste) == (
        "fragmented_edits:e3",
        "fragmented_edits",
        "motion",
    )
    assert entry.confidence == 0.8 and entry.position == 3
    assert entry.rule.startswith("0.80 when")
    assert entry.evidence == ("e1", "e3")
    assert entry.advice_kind is AdviceKind.RECOMMENDATION
    assert entry.advice == "Make the change in one edit."
    assert entry.observation is None


@pytest.mark.req("TER-INT-009")
def test_a_suppressed_verdict_records_the_reason_and_no_action() -> None:
    [verdict] = InterventionEngine((policy(),), ELIGIBLE).apply(signal(confidence=0.6))
    entry = LedgerRecord.from_verdict(verdict, "s1", AT)
    assert entry.outcome is Outcome.LOW_CONFIDENCE and not entry.delivered
    assert entry.action is None and entry.advice_kind is None and not entry.advice
    assert "below 0.70" in entry.reason


@pytest.mark.req("TER-INT-009")
def test_the_outcome_is_recorded_with_the_intervention() -> None:
    entry = advised().observed(observation())
    assert entry.observation is not None
    assert entry.observation.response is Response.ACCEPTED
    assert entry.observation.before["rework_ratio"] == 0.5
    with pytest.raises(LedgerError, match="already has its outcome"):
        entry.observed(observation(response=Response.IGNORED))


@pytest.mark.req("TER-INT-009")
def test_the_record_id_names_the_decision() -> None:
    a = advised()
    assert a.id == advised().id  # replaying the same signal gives the same id
    assert a.id.startswith("int-")
    other = record_id("s1", "batch-edits", a.signal_id, (0.9, ("e1",)), Outcome.ADVISED)
    assert other != a.id
    assert (
        record_id("s2", "batch-edits", a.signal_id, (0.8, ("e1", "e3")), a.outcome)
        != a.id
    )


@pytest.mark.req("TER-INT-009")
def test_a_record_round_trips_through_its_mapping() -> None:
    entry = advised()
    again = LedgerRecord.from_mapping(json.loads(json.dumps(entry.as_dict())))
    assert again == entry
    obs = observation()
    assert Observation.from_mapping(json.loads(json.dumps(obs.as_dict()))) == obs


@pytest.mark.req("TER-INT-009", "TER-INT-007")
def test_no_action_is_recorded_without_evidence() -> None:
    with pytest.raises(LedgerError, match="without evidence"):
        replace(advised(), evidence=())


@pytest.mark.parametrize(
    "change",
    [
        {"action": None},
        {"outcome": Outcome.COOLDOWN},
        {"advice": " "},
        {"advice_kind": None},
        {"policy": ""},
        {"signal_id": ""},
    ],
)
def test_inconsistent_records_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(LedgerError):
        replace(advised(), **change)


@pytest.mark.parametrize(
    "change",
    [
        {"tool_events": -1},
        {"before": {"": 1.0}},
        {"after": {"flow": "fast"}},
        {"after": {"flow": True}},
    ],
)
def test_malformed_observations_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(LedgerError):
        observation(**change)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("signal"),
        lambda d: d.update(outcome="maybe"),
        lambda d: d.update(evidence="e1"),
        lambda d: d["signal"].update(position=1.5),
        lambda d: d["signal"].update(confidence=True),
        lambda d: d.update(recorded_at="yesterday"),
    ],
)
def test_malformed_mappings_are_refused(change: Any) -> None:
    data = json.loads(json.dumps(advised().as_dict()))
    change(data)
    with pytest.raises(LedgerError):
        LedgerRecord.from_mapping(data)


def test_a_bad_observation_mapping_is_refused() -> None:
    with pytest.raises(LedgerError, match="not a ledger observation"):
        Observation.from_mapping({"response": "loved"})


@pytest.mark.req("TER-INT-009")
def test_the_ledger_loads_through_the_capability_registry(tmp_path: Path) -> None:
    ledger = bootstrap.make_ledger(tmp_path)
    assert isinstance(ledger, InterventionLedger)
    assert isinstance(ledger, JsonlInterventionLedger)
    assert "jsonl" in bootstrap.default_registry().names("InterventionLedger")
    ledger.record(advised())
    assert bootstrap.make_ledger(tmp_path).entries("s1") == (advised(),)
