"""L4 Advisory: live advice (TER-INT-003, TER-INT-014).

A session's detector signals meet the approved policies; each advised
decision is ledgered and delivered once, with the signal and evidence behind
it. Asking again delivers only what is new.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ter import bootstrap
from ter.adapters.driven.in_memory import (
    FixedClock,
    InMemoryInterventionChannel,
    InMemoryInterventionLedger,
)
from ter.application.advise import advise_session, decide
from ter.domain.intervention import Outcome
from ter.domain.lean import LeanAnalysis
from ter.domain.maturity import Maturity
from ter.domain.policy_set import PolicySet, UnapprovedPolicyError

REPO = Path(__file__).resolve().parents[2]
SESSION = REPO / "sample_sessions" / "example_session.jsonl"
EXAMPLE = REPO / "docs" / "examples" / "policies" / "l4-advisory.json"
AT = datetime(2026, 10, 12, 19, 0, tzinfo=UTC)
SID = "sample-session-001"


def draft() -> PolicySet:
    return PolicySet.from_mapping(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def approved() -> PolicySet:
    return draft().approve("Test", date(2026, 10, 12))


@pytest.fixture(scope="module")
def analysis() -> LeanAnalysis:
    services = bootstrap.cli_services()
    assert services.explain_transcript is not None
    return services.explain_transcript(SESSION, "regex", "off").analysis


@pytest.mark.req("TER-INT-003")
def test_an_advised_signal_issues_the_policys_advice(analysis: LeanAnalysis) -> None:
    channel = InMemoryInterventionChannel()
    ledger = InMemoryInterventionLedger()
    decisions = advise_session(
        SID, analysis, approved(), ledger, channel, FixedClock(AT)
    )
    [advice] = channel.interventions
    assert advice.record.policy == "reuse-reads"
    assert advice.record.advice == next(
        p.advice.message for p in approved().policies if p.id == "reuse-reads"
    )
    assert advice.record.policy_version == approved().digest()
    assert ledger.entries(SID) == decisions.records
    # Suppressed decisions are ledgered, never delivered.
    assert Outcome.INELIGIBLE in {r.outcome for r in decisions.records}
    assert len(decisions.records) > len(decisions.interventions)


@pytest.mark.req("TER-INT-003")
def test_asking_again_delivers_nothing_twice(analysis: LeanAnalysis) -> None:
    ledger = InMemoryInterventionLedger()
    first = InMemoryInterventionChannel()
    advise_session(SID, analysis, approved(), ledger, first, FixedClock(AT))
    again = InMemoryInterventionChannel()
    decisions = advise_session(SID, analysis, approved(), ledger, again, FixedClock(AT))
    assert again.interventions == []
    assert decisions.written == () and decisions.new_interventions == ()
    assert len(ledger.entries(SID)) == len(decisions.records)
    assert len(first.interventions) == 1


@pytest.mark.req("TER-INT-003")
def test_decide_matches_shadow_and_delivers_nothing(analysis: LeanAnalysis) -> None:
    ledger = InMemoryInterventionLedger()
    decisions = decide(SID, analysis, approved(), ledger, FixedClock(AT))
    assert decisions.written == decisions.records
    assert [i.record.policy for i in decisions.new_interventions] == ["reuse-reads"]


@pytest.mark.req("TER-INT-003", "TER-INT-015")
def test_unapproved_policies_decide_nothing(analysis: LeanAnalysis) -> None:
    ledger = InMemoryInterventionLedger()
    channel = InMemoryInterventionChannel()
    with pytest.raises(UnapprovedPolicyError):
        advise_session(SID, analysis, draft(), ledger, channel, FixedClock(AT))
    assert ledger.entries(SID) == () and channel.interventions == []


@pytest.mark.req("TER-INT-001", "TER-INT-003")
def test_below_the_advisory_ceiling_nothing_is_advised(
    analysis: LeanAnalysis,
) -> None:
    channel = InMemoryInterventionChannel()
    decisions = advise_session(
        SID,
        analysis,
        approved(),
        InMemoryInterventionLedger(),
        channel,
        FixedClock(AT),
        ceiling=Maturity.EXPLAINED,
    )
    assert channel.interventions == [] and decisions.interventions == ()
    assert decisions.records  # still ledgered, with the reason


@pytest.mark.req("TER-INT-014")
def test_every_delivery_carries_its_signal_and_evidence(
    analysis: LeanAnalysis,
) -> None:
    channel = InMemoryInterventionChannel()
    advise_session(
        SID,
        analysis,
        approved(),
        InMemoryInterventionLedger(),
        channel,
        FixedClock(AT),
    )
    [advice] = channel.interventions
    [message] = channel.delivered()
    assert advice.record.signal_id == advice.signal.id
    assert advice.record.evidence and all(
        e in message for e in advice.record.evidence[:4]
    )
    assert advice.signal.title in message and advice.record.detector in message
    assert f"{advice.record.confidence:.2f}" in message
