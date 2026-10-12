"""L4 Advisory: did the advice help? (TER-INT-011, TER-INT-012).

Delivered advice is followed up once ``WINDOW`` tool events have been
observed after it: the response (accepted or ignored, inferred from whether
the waste it named came back) and the flow measures before and after it.
Each policy is then scored from those outcomes, and every policy that does
not improve them is flagged for recalibration.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from ter.domain.effectiveness import (
    MIN_OBSERVED,
    REPEATS,
    WASTE,
    WINDOW,
    Effect,
    effectiveness,
    follow_up,
)
from ter.domain.intervention import (
    Advice,
    AdviceKind,
    Eligibility,
    InterventionEngine,
    Policy,
    Signal,
)
from ter.domain.ledger import LedgerError, LedgerRecord, Observation, Response

AT = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)
DETECTORS = frozenset({"repeated_exploration"})
POLICY = Policy(
    "reuse-reads",
    DETECTORS,
    Advice(AdviceKind.WARNING, "Reuse the earlier read."),
)


def signal(
    position: int,
    *,
    sid: str | None = None,
    detector: str = "repeated_exploration",
    confidence: float = 0.85,
    kind: str = "waste",
) -> Signal:
    return Signal(
        id=sid or f"{detector}:e{position}",
        detector=detector,
        waste="motion",
        kind=kind,
        confidence=confidence,
        title=f"Re-read src/a{position}.py",
        subject=f"src/a{position}.py",
        evidence=(f"e{position - 1}", f"e{position}"),
        rule="0.85 when the same read returned identical output",
        position=position,
    )


def advised(
    position: int = 20, delivered_at: int | None = 22, **kw: Any
) -> LedgerRecord:
    engine = InterventionEngine((POLICY,), Eligibility(DETECTORS, "judged"))
    [verdict] = engine.apply(signal(position))
    record = LedgerRecord.from_verdict(verdict, "s1", AT, "sha256:v1", delivered_at)
    return replace(record, **kw) if kw else record


def observe(
    record: LedgerRecord, signals: list[Signal], tool_events: int
) -> Observation | None:
    return follow_up(record, signals, tool_events, DETECTORS, AT)


class TestFollowUp:
    @pytest.mark.req("TER-INT-011")
    def test_waits_for_ten_tool_events_after_the_delivery(self) -> None:
        record = advised()
        assert observe(record, [signal(20)], 22 + WINDOW - 1) is None
        assert observe(record, [signal(20)], 22 + WINDOW) is not None

    @pytest.mark.req("TER-INT-011")
    def test_no_repeat_after_the_advice_is_accepted(self) -> None:
        signals = [signal(14), signal(20), signal(33)]  # 33 is after the window
        observation = observe(advised(), signals, 40)
        assert observation is not None
        assert observation.response is Response.ACCEPTED
        assert observation.tool_events == 18
        # One repeat in the 10 tool events before delivery (14; 20 is the
        # advised signal itself), none in the 10 after.
        assert observation.before == {REPEATS: 0.1, WASTE: 0.2}
        assert observation.after == {REPEATS: 0.0, WASTE: 0.0}

    @pytest.mark.req("TER-INT-011")
    def test_a_repeat_after_the_advice_is_ignored(self) -> None:
        observation = observe(advised(), [signal(20), signal(25)], 32)
        assert observation is not None
        assert observation.response is Response.IGNORED
        assert observation.after[REPEATS] == 0.1

    @pytest.mark.req("TER-INT-011")
    def test_the_advised_finding_growing_after_the_advice_is_ignored(self) -> None:
        # More round trips on the same file extend one finding, under its id.
        grown = replace(signal(26), id="repeated_exploration:e20")
        observation = observe(advised(), [grown], 32)
        assert observation is not None and observation.response is Response.IGNORED
        assert observation.after[REPEATS] == 0.1
        later = replace(signal(40), id="repeated_exploration:e20")  # past the window
        observation = observe(advised(), [later], 40)
        assert observation is not None and observation.response is Response.IGNORED

    @pytest.mark.req("TER-INT-011")
    def test_the_advised_finding_unchanged_is_not_a_repeat(self) -> None:
        observation = observe(advised(), [signal(20)], 32)
        assert observation is not None and observation.response is Response.ACCEPTED
        assert observation.before[REPEATS] == 0.0

    @pytest.mark.req("TER-INT-011")
    def test_an_uncertain_or_other_detector_signal_is_no_repeat_but_is_waste(
        self,
    ) -> None:
        signals = [
            signal(20),
            signal(24, confidence=0.55),
            signal(25, detector="fragmented_edits"),
            signal(26, detector="unverified_edit", kind="risk"),
        ]
        observation = observe(advised(), signals, 32)
        assert observation is not None
        assert observation.response is Response.ACCEPTED
        assert observation.after == {REPEATS: 0.0, WASTE: 0.2}

    @pytest.mark.req("TER-INT-011")
    def test_a_short_session_start_is_measured_per_tool_event(self) -> None:
        observation = observe(advised(position=3, delivered_at=4), [signal(2)], 14)
        assert observation is not None
        assert observation.before[REPEATS] == 0.25  # 1 repeat over 4 tool events

    @pytest.mark.req("TER-INT-011")
    def test_shadow_advice_is_never_followed_up(self) -> None:
        record = advised(delivered_at=None)  # shadow mode: never shown
        assert record.delivered
        assert observe(record, [signal(20)], 100) is None

    @pytest.mark.req("TER-INT-011")
    def test_suppressed_or_already_observed_records_are_left_alone(self) -> None:
        engine = InterventionEngine((POLICY,), Eligibility(DETECTORS, "judged"))
        [low] = engine.apply(signal(20, confidence=0.6))
        suppressed = LedgerRecord.from_verdict(low, "s1", AT, "sha256:v1", 22)
        assert suppressed.delivered_at is None
        assert observe(suppressed, [], 100) is None
        done = advised().observed(Observation(Response.ACCEPTED, AT, 10))
        assert observe(done, [], 100) is None


class TestDeliveredAt:
    @pytest.mark.req("TER-INT-011")
    def test_round_trips_and_is_absent_when_not_delivered_live(self) -> None:
        record = advised()
        assert record.as_dict()["delivered_at"] == 22
        assert (
            LedgerRecord.from_mapping(json.loads(json.dumps(record.as_dict())))
            == record
        )
        assert "delivered_at" not in advised(delivered_at=None).as_dict()

    @pytest.mark.req("TER-INT-011")
    def test_a_negative_delivery_position_is_refused(self) -> None:
        with pytest.raises(LedgerError, match="delivery position"):
            advised(delivered_at=-1)


def outcome(
    response: Response,
    before: float = 0.2,
    after: float = 0.0,
    version: str = "sha256:v1",
) -> LedgerRecord:
    return replace(advised(), policy_version=version).observed(
        Observation(response, AT, WINDOW, {REPEATS: before}, {REPEATS: after})
    )


class TestEffectiveness:
    @pytest.mark.req("TER-INT-012")
    def test_too_few_outcomes_are_unproven(self) -> None:
        records = [outcome(Response.ACCEPTED)] * (MIN_OBSERVED - 1) + [advised()]
        [score] = effectiveness(records)
        assert (score.advised, score.observed) == (MIN_OBSERVED, MIN_OBSERVED - 1)
        assert score.effect is Effect.UNPROVEN and not score.recalibrate
        assert "4 observed outcome(s); 5 are needed" in score.why

    @pytest.mark.req("TER-INT-012")
    def test_accepted_advice_with_fewer_repeats_improves(self) -> None:
        records = [outcome(Response.ACCEPTED)] * 4 + [
            outcome(Response.IGNORED, 0.1, 0.1)
        ]
        [score] = effectiveness(records)
        assert score.effect is Effect.IMPROVES and not score.recalibrate
        assert score.acceptance == 0.8
        assert (score.repeats_before, score.repeats_after) == (0.18, 0.02)

    @pytest.mark.req("TER-INT-012")
    def test_mostly_ignored_advice_is_flagged(self) -> None:
        records = [outcome(Response.IGNORED, 0.2, 0.1)] * 3 + [
            outcome(Response.ACCEPTED)
        ] * 2
        [score] = effectiveness(records)
        assert score.effect is Effect.NO_IMPROVEMENT and score.recalibrate
        assert score.why.startswith("does not improve outcomes: 40% accepted")

    @pytest.mark.req("TER-INT-012")
    def test_repeats_rising_after_the_advice_is_flagged(self) -> None:
        records = [outcome(Response.ACCEPTED, 0.0, 0.0)] * 4 + [
            outcome(Response.IGNORED, 0.0, 0.3)
        ]
        [score] = effectiveness(records)
        assert score.effect is Effect.NO_IMPROVEMENT and score.recalibrate

    @pytest.mark.req("TER-INT-012")
    def test_each_policy_file_is_scored_on_its_own(self) -> None:
        records = [outcome(Response.ACCEPTED)] * 5 + [
            outcome(Response.IGNORED, version="sha256:v2")
        ]
        scores = effectiveness(records)
        assert [(s.policy_version, s.effect) for s in scores] == [
            ("sha256:v1", Effect.IMPROVES),
            ("sha256:v2", Effect.UNPROVEN),
        ]

    @pytest.mark.req("TER-INT-012")
    def test_shadow_advice_is_not_scored(self) -> None:
        assert effectiveness([advised(delivered_at=None)]) == ()
        [score] = effectiveness([advised(), advised(delivered_at=None)])
        assert score.advised == 1

    @pytest.mark.req("TER-INT-012")
    def test_suppressed_decisions_are_not_scored(self) -> None:
        engine = InterventionEngine((POLICY,), Eligibility(frozenset(), "none"))
        [ineligible] = engine.apply(signal(20))
        assert effectiveness([LedgerRecord.from_verdict(ineligible, "s1", AT)]) == ()
