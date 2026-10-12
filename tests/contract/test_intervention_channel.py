"""Contract suite for the ``InterventionChannel`` port (TER-INT-002, TER-INT-014).

The message channel and the in-memory fake run the same assertions, so the
fake cannot drift from the obligations the real adapter meets.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from ter.adapters.driven.in_memory import InMemoryInterventionChannel
from ter.adapters.driven.intervention_channel import MessageChannel
from ter.domain.intervention import (
    Advice,
    AdviceKind,
    Eligibility,
    Policy,
    Signal,
    decide,
)
from ter.domain.ledger import Intervention, LedgerError, LedgerRecord
from ter.ports import InterventionChannel

AT = datetime(2026, 10, 12, 17, 30, tzinfo=UTC)

POLICY = Policy(
    "reuse-reads",
    frozenset({"repeated_exploration"}),
    Advice(AdviceKind.WARNING, "Reuse the earlier read."),
    min_evidence=2,
    cooldown_tool_events=5,
)


def signal(n: int, *, evidence: int = 2, confidence: float = 0.85) -> Signal:
    return Signal(
        id=f"repeated_exploration:s1-e{n}",
        detector="repeated_exploration",
        waste="motion",
        kind="waste",
        confidence=confidence,
        title=f"Re-read src/a{n}.py",
        subject=f"src/a{n}.py",
        evidence=tuple(f"s1-e{n - i}" for i in reversed(range(evidence))),
        rule="0.85 when the same read returned identical output",
        position=n,
    )


def interventions(*signals: Signal) -> tuple[Intervention, ...]:
    verdicts = decide(
        signals, [POLICY], Eligibility(frozenset({"repeated_exploration"}), "judged")
    )
    by_id = {s.id: s for s in signals}
    out = []
    for verdict in verdicts:
        record = LedgerRecord.from_verdict(verdict, "s1", AT, "sha256:abc")
        if record.delivered:
            out.append(Intervention(record, by_id[record.signal_id]))
    return tuple(out)


@pytest.fixture(
    params=[MessageChannel, InMemoryInterventionChannel],
    ids=["message", "in-memory"],
)
def channel(request: pytest.FixtureRequest) -> InterventionChannel:
    factory: Callable[[], InterventionChannel] = request.param
    return factory()


@pytest.mark.req("TER-INT-002")
def test_satisfies_the_port_protocol(channel: InterventionChannel) -> None:
    assert isinstance(channel, InterventionChannel) and channel.name
    assert channel.delivered() == ()


@pytest.mark.req("TER-INT-014")
def test_each_message_shows_the_advice_signal_and_evidence(
    channel: InterventionChannel,
) -> None:
    (advice,) = interventions(signal(2))
    channel.deliver(advice)
    (message,) = channel.delivered()
    assert message == (
        "TER warning (reuse-reads): Reuse the earlier read.\n"
        "Why: Re-read src/a2.py (repeated_exploration, confidence 0.85: "
        "0.85 when the same read returned identical output)\n"
        "Evidence: s1-e1, s1-e2"
    )


@pytest.mark.req("TER-INT-014")
def test_long_evidence_is_shortened_with_a_count(
    channel: InterventionChannel,
) -> None:
    (advice,) = interventions(signal(9, evidence=7))
    channel.deliver(advice)
    (message,) = channel.delivered()
    assert message.endswith("Evidence: s1-e3, s1-e4, s1-e5, s1-e6 and 3 more")


@pytest.mark.req("TER-INT-002")
def test_deliveries_are_kept_in_order(channel: InterventionChannel) -> None:
    first, second = interventions(signal(2), signal(20))
    channel.deliver(first)
    channel.deliver(second)
    assert [m.splitlines()[1].split(" (")[0] for m in channel.delivered()] == [
        "Why: Re-read src/a2.py",
        "Why: Re-read src/a20.py",
    ]


@pytest.mark.req("TER-INT-002")
def test_a_suppressed_decision_cannot_be_delivered() -> None:
    signals = (signal(2), signal(4))  # the second falls in the cooldown
    verdicts = decide(
        signals, [POLICY], Eligibility(frozenset({"repeated_exploration"}), "judged")
    )
    cooled = LedgerRecord.from_verdict(verdicts[1], "s1", AT)
    assert not cooled.delivered
    with pytest.raises(LedgerError, match="not advised"):
        Intervention(cooled, signals[1])


@pytest.mark.req("TER-INT-014")
def test_an_intervention_names_its_own_signal() -> None:
    (advice,) = interventions(signal(2))
    with pytest.raises(LedgerError, match="is about"):
        Intervention(advice.record, signal(3))
