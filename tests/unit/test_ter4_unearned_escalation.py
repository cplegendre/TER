"""The ``unearned_escalation`` detector (TER-DET-011, point 30): a model
escalation after a completed model call is waiting waste unless the escalated
call adds evidence the earlier call did not. Positive, negative and boundary
cases, on recorded ``route.escalated`` events and on a routing harness's
re-attempts (``attempt.started``) served by another model."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from ter4_lean_builder import FAIL, PASS, Script

from ter.adapters.driven.gare import GareRunSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.domain import EventKind, TokenUsage
from ter.domain.events import Event
from ter.domain.lean import (
    ActivityClass,
    Finding,
    FindingKind,
    FlowState,
    LeanAnalysis,
    LeanWaste,
    build_countermeasures,
    explain,
)
from ter.domain.lean.countermeasures import follow_ups
from ter.domain.lean.detectors import DEFAULT_REGISTRY
from ter.domain.routing import RouteEscalation

GARE = Path(__file__).parents[1] / "fixtures" / "gare"
ESCALATED = RouteEscalation(
    signal="rework_cycle",
    finding="rework_cycle:abc",
    source_role="implement",
    target_role="escalate",
    latency_ms=4000,
    input_tokens=1200,
    output_tokens=300,
).text()


def run(s: Script) -> LeanAnalysis:
    return explain(s.events, RegexTokenizer())


def found(s: Script) -> list[Finding]:
    return [f for f in run(s).findings if f.detector == "unearned_escalation"]


def served(s: Script, event: Event, model: str) -> Event:
    """Give a response event the usage of the model that served it."""
    i = s.events.index(event)
    s.events[i] = replace(
        event, usage=TokenUsage(input_tokens=100, output_tokens=50, model=model)
    )
    return s.events[i]


def answered_then_escalated(s: Script) -> tuple[Event, Event]:
    s.prompt("fix the rounding bug in src/a.py")
    s.read("src/a.py")
    s.bash("pytest -q", FAIL)
    earlier = s.say("I think the rounding is in round_total.")
    mark = s.lifecycle(EventKind.ROUTE_ESCALATED, ESCALATED)
    return earlier, mark


@pytest.mark.req("TER-DET-011")
class TestRecordedEscalation:
    def test_an_escalation_that_adds_nothing_is_waiting(self) -> None:
        s = Script()
        earlier, mark = answered_then_escalated(s)
        again = s.say("The rounding is in round_total.")
        [f] = found(s)
        assert f.waste is LeanWaste.WAITING and f.kind is FindingKind.WASTE
        assert f.confidence == 0.8 and not f.uncertain
        assert f.evidence == (earlier.id, mark.id, again.id)
        assert f.waste_events == (mark.id, again.id)
        assert f.id == f"unearned_escalation:{mark.id}"
        assert f.seconds == 2.0  # the marker's and the escalated call's wall time

    def test_the_escalated_call_lands_in_the_waiting_flow_state(self) -> None:
        s = Script()
        _, mark = answered_then_escalated(s)
        s.say("The rounding is in round_total.")
        a = run(s)
        [c] = [c for c in a.classifications if c.event_id == mark.id]
        assert c.flow is FlowState.WAITING
        assert c.activity_class is ActivityClass.AVOIDABLE
        assert dict(a.scorecard.flow_seconds)[FlowState.WAITING] >= 2.0

    def test_reading_a_new_file_adds_evidence(self) -> None:
        s = Script()
        answered_then_escalated(s)
        s.read("src/rounding.py")
        s.say("Fixed in rounding.py.")
        assert found(s) == []

    def test_a_new_check_result_adds_evidence(self) -> None:
        s = Script()
        answered_then_escalated(s)
        s.edit("src/a.py")
        s.bash("pytest -q", PASS)
        s.say("Fixed; tests pass.")
        assert found(s) == []

    def test_a_new_tool_output_adds_evidence(self) -> None:
        s = Script()
        answered_then_escalated(s)
        s.bash("git log -1 --format=%s", "Round totals to cents")
        s.say("The last commit changed rounding.")
        assert found(s) == []

    def test_boundary_rereading_the_same_file_and_rerunning_the_same_failure(
        self,
    ) -> None:
        # The same file read again and the same check failing the same way
        # are evidence the earlier call already had: still waiting waste.
        s = Script()
        answered_then_escalated(s)
        s.read("src/a.py")
        s.bash("pytest -q", FAIL)
        s.edit("src/a.py")
        s.say("Changed round_total.")
        [f] = found(s)
        assert f.confidence == 0.8

    def test_boundary_evidence_after_the_next_prompt_does_not_count(self) -> None:
        s = Script()
        answered_then_escalated(s)
        s.say("The rounding is in round_total.")
        s.prompt("now look at src/b.py")
        s.read("src/b.py")
        assert len(found(s)) == 1

    def test_no_completed_call_before_the_escalation_no_finding(self) -> None:
        s = Script()
        s.prompt("fix the rounding bug in src/a.py")
        s.read("src/a.py")
        s.lifecycle(EventKind.ROUTE_ESCALATED, ESCALATED)
        s.say("The rounding is in round_total.")
        assert found(s) == []

    def test_a_response_of_an_earlier_task_is_not_the_completed_call(self) -> None:
        s = Script()
        s.prompt("summarise the module")
        s.say("It rounds totals.")
        s.prompt("fix the rounding")
        s.lifecycle(EventKind.ROUTE_ESCALATED, ESCALATED)
        s.say("Changed round_total.")
        assert found(s) == []

    def test_boundary_no_escalated_response_is_uncertain(self) -> None:
        s = Script()
        earlier, mark = answered_then_escalated(s)
        [f] = found(s)
        assert f.uncertain and f.confidence == 0.5
        assert f.evidence == (earlier.id, mark.id) and f.waste_events == (mark.id,)

    def test_each_escalation_is_judged_on_its_own_window(self) -> None:
        s = Script()
        answered_then_escalated(s)
        s.say("The rounding is in round_total.")
        second = s.lifecycle(EventKind.ROUTE_ESCALATED, ESCALATED + " ")
        s.read("src/rounding.py")
        s.say("Fixed in rounding.py.")
        [f] = found(s)
        assert second.id not in f.evidence

    def test_no_escalation_no_finding(self) -> None:
        s = Script()
        s.prompt("fix the rounding bug in src/a.py")
        s.say("Done.")
        s.say("Also done.")
        assert found(s) == []


@pytest.mark.req("TER-DET-011")
class TestReattemptOnAnotherModel:
    def test_a_reattempt_served_by_another_model_is_uncertain_waiting(self) -> None:
        s = Script()
        s.prompt("Fix add so it returns the sum")
        s.lifecycle(EventKind.ATTEMPT_STARTED, "attempt 1 coder-1")
        first = served(s, s.say("coder-1: local/small"), "small")
        s.lifecycle(EventKind.ROUTE_SELECTED, "coder-2: cloud/large")
        s.lifecycle(EventKind.ATTEMPT_STARTED, "attempt 2 coder-2")
        second = served(s, s.say("coder-2: cloud/large"), "large")
        [f] = found(s)
        assert f.uncertain and f.confidence == 0.6
        assert f.evidence == (first.id, second.id)
        assert f.waste_events == (second.id,)
        assert "(large)" in f.explanation

    def test_a_reattempt_on_the_same_model_is_not_an_escalation(self) -> None:
        s = Script()
        s.prompt("Fix add so it returns the sum")
        s.lifecycle(EventKind.ATTEMPT_STARTED, "attempt 1 coder-1")
        served(s, s.say("coder-1: local/small"), "small")
        s.lifecycle(EventKind.ATTEMPT_STARTED, "attempt 2 coder-2")
        served(s, s.say("coder-2: local/small"), "small")
        assert found(s) == []

    def test_another_model_without_a_new_attempt_is_not_an_escalation(self) -> None:
        # Two roles of one run (a coder, then a reviewer) on different models.
        s = Script()
        s.prompt("Fix add so it returns the sum")
        s.lifecycle(EventKind.ATTEMPT_STARTED, "attempt 1 coder-1")
        served(s, s.say("coder-1: local/small"), "small")
        s.lifecycle(EventKind.ROUTE_SELECTED, "review-1: cloud/large")
        served(s, s.say("review-1: cloud/large"), "large")
        assert found(s) == []

    def test_a_failover_is_left_to_failed_route(self) -> None:
        s = Script()
        s.prompt("research the API")
        s.lifecycle(EventKind.ATTEMPT_STARTED, "attempt 1 research-1")
        s.failover("research-1: flaky/large failed")
        served(s, s.say("research-1: mock/smart"), "smart")
        a = run(s)
        assert [f.detector for f in a.findings if "route" in f.detector] == [
            "failed_route"
        ]
        assert not [f for f in a.findings if f.detector == "unearned_escalation"]

    @pytest.mark.parametrize(
        "run_dir", ["failover-run", "repair-mission", "runs/c2ffdf8f1b09"]
    )
    def test_recorded_gare_runs_hold_no_escalation(self, run_dir: str) -> None:
        # Every attempt of these runs was served by one model, so none
        # escalated; the real run's second coder attempt is a re-attempt on
        # the same local model.
        trace = GareRunSource().read(GARE / run_dir)
        a = explain(trace.events, RegexTokenizer())
        assert not [f for f in a.findings if f.detector == "unearned_escalation"]
        if run_dir.startswith("runs/"):
            assert sum(1 for s in a.steps if s.opens_attempt) == 2


@pytest.mark.req("TER-DET-011")
def test_registered_with_a_countermeasure_a_follow_up_and_a_rule() -> None:
    assert "unearned_escalation" in DEFAULT_REGISTRY
    detector = DEFAULT_REGISTRY.get("unearned_escalation")
    assert detector.waste is LeanWaste.WAITING
    assert "0.80" in detector.confidence_rule and "0.60" in detector.confidence_rule
    s = Script()
    answered_then_escalated(s)
    s.say("The rounding is in round_total.")
    a = run(s)
    [cm] = [
        c
        for c in build_countermeasures(a.findings, a.steps)
        if c.detector == "unearned_escalation"
    ]
    assert cm.actions and "evidence" in cm.actions[0].text
    follow = follow_ups(a.findings, flow_efficiency=None, avoidable_share=0.0)
    assert "findings[detector=unearned_escalation]" in {f.how for f in follow}


@pytest.mark.req("TER-DET-011")
def test_batch_equals_incremental_with_escalations() -> None:
    from ter.domain import AnalysisEngine

    s = Script()
    answered_then_escalated(s)
    s.say("The rounding is in round_total.")
    batch = run(s)
    engine = AnalysisEngine(RegexTokenizer())
    for event in s.events:
        engine.apply(event)
        engine.apply(event)  # redelivery changes nothing
    live = engine.explain()
    assert live.findings == batch.findings
    assert live.steps == batch.steps
