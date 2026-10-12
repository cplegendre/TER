"""L4 Advisory: control-chart signals as policy triggers (TER-SPC-020).

A session placed against its control limits gives a firing control signal
for each measure outside a limit the way it gets worse. A policy that names
that signal's id (``control.<measure>.<rule>``) answers it like any detector
signal, with the events of the findings behind its measure as evidence.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
from ter4_lean_builder import FAIL, PASS, Script

from ter.adapters.driven.in_memory import (
    FixedClock,
    InMemoryInterventionChannel,
    InMemoryInterventionLedger,
)
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application.loop import ControlLoop
from ter.domain.intervention import Outcome
from ter.domain.lean import explain
from ter.domain.lean.control import (
    ControlLimits,
    ControlRule,
    LimitMethod,
    MeasureLimits,
    NaturalLimits,
    control_measure,
    LIVE_MEASURES,
    detector_fingerprint,
    session_control,
)
from ter.domain.lean.signals import CONTROL_KIND, control_signals, detector_signals
from ter.domain.policy_set import PolicySet

AT = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
REWORK = "control.rework_cycles.beyond_limits"


def rework_session() -> Any:
    s = Script()
    s.prompt("fix the failing test")
    s.read("src/app.py", "def f(): return 1")
    s.bash("pytest", FAIL)
    s.edit("src/app.py", "return 1", "return 2")
    s.bash("pytest", FAIL)
    s.edit("src/app.py", "return 2", "return 3")
    s.bash("pytest", PASS)
    s.say("Fixed.")
    analysis = explain(s.events, RegexTokenizer())
    assert analysis.scorecard.rework_cycles == 1
    return analysis


def measure_limits(
    key: str, centre: float, sigma: float, ucl: float | None, lcl: float | None
) -> MeasureLimits:
    natural = NaturalLimits(
        centre,
        ucl,
        lcl,
        sigma,
        sigma * 1.128,
        sigma * 1.128 * 3.268,
        30,
        LimitMethod.AVERAGE_MOVING_RANGE,
    )
    return MeasureLimits(control_measure(key), natural, None, frozenset(ControlRule))


def limits(detectors: str, *entries: MeasureLimits) -> ControlLimits:
    return ControlLimits(entries, LimitMethod.AVERAGE_MOVING_RANGE, detectors, 30)


def rework_beyond(analysis: Any, detectors: str | None = None) -> ControlLimits:
    """Limits the session's one rework cycle is above."""
    return limits(
        detectors or detector_fingerprint(analysis),
        measure_limits("rework_cycles", 0.0, 0.2, 0.5, None),
    )


def policies(eligible: tuple[str, ...] = (REWORK,)) -> PolicySet:
    return PolicySet.from_mapping(
        {
            "schema": "ter.intervention-policies/1",
            "eligibility": {
                "detectors": list(eligible),
                "basis": "test: the owner's control baseline",
            },
            "policies": [
                {
                    "id": "rework-above-limit",
                    "detectors": [REWORK],
                    "advice": {
                        "kind": "warning",
                        "message": "This session has more rework than usual.",
                    },
                }
            ],
        }
    ).approve("Test", date(2026, 10, 12))


class TestControlSignals:
    @pytest.mark.req("TER-SPC-020")
    def test_a_firing_control_signal_cites_the_events_behind_its_measure(
        self,
    ) -> None:
        analysis = rework_session()
        control = session_control(analysis, rework_beyond(analysis))
        [signal] = control_signals(control, analysis)
        rework = [
            f for f in analysis.findings if f.id in control.behind["rework_cycles"]
        ]
        cited = {e for f in rework for e in f.evidence}
        assert signal.detector == REWORK and signal.id == f"{REWORK}:s"
        assert signal.kind == CONTROL_KIND and set(signal.evidence) == cited
        assert signal.confidence == 1.0
        # Positioned at its last cited event, like a finding.
        assert signal.position == max(
            s.position
            for s in detector_signals(analysis)
            if s.id in {f.id for f in rework}
        )
        assert signal.rule == ControlRule.BEYOND_LIMITS.description
        assert signal.subject == "rework_cycles"
        assert "above its limit 0.5" in signal.title

    @pytest.mark.req("TER-SPC-020", "TER-SPC-006")
    def test_a_favourable_signal_never_becomes_one(self) -> None:
        analysis = rework_session()
        below = limits(
            detector_fingerprint(analysis),
            measure_limits("rework_cycles", 5.0, 1.0, 8.0, 2.0),
        )
        control = session_control(analysis, below)
        [placed] = control.placements
        assert placed.signal is not None and not placed.signal.unfavourable
        assert control_signals(control, analysis) == ()

    @pytest.mark.req("TER-SPC-020", "TER-INT-007")
    def test_a_token_total_never_becomes_one(self) -> None:
        analysis = rework_session()
        tokens = limits(
            detector_fingerprint(analysis),
            measure_limits("generated_tokens", 0.0, 0.1, 0.3, None),
        )
        control = session_control(analysis, tokens)
        [placed] = control.placements
        assert placed.signal is not None and not placed.signal.fires
        assert control_signals(control, analysis) == ()

    @pytest.mark.req("TER-SPC-020", "TER-SPC-007")
    def test_stale_limits_give_none(self) -> None:
        analysis = rework_session()
        control = session_control(analysis, rework_beyond(analysis, "other"))
        assert control.stale and control.firing
        assert control_signals(control, analysis) == ()

    @pytest.mark.req("TER-SPC-020")
    def test_live_only_counts_are_placed(self) -> None:
        analysis = rework_session()
        share = limits(
            detector_fingerprint(analysis),
            measure_limits("avoidable_share", 0.0, 0.001, 0.003, None),
            measure_limits("rework_cycles", 0.0, 0.2, 0.5, None),
        )
        control = session_control(analysis, share)
        assert {s.measure for s in control.firing} == {
            "avoidable_share",
            "rework_cycles",
        }
        finished = control_signals(control, analysis)
        assert {s.subject for s in finished} == {"avoidable_share", "rework_cycles"}
        # An unfinished session is judged on counts only (LIVE_MEASURES).
        assert "rework_cycles" in LIVE_MEASURES
        assert "avoidable_share" not in LIVE_MEASURES
        live = control_signals(control, analysis, live=True)
        assert [s.subject for s in live] == ["rework_cycles"]

    @pytest.mark.req("TER-SPC-020", "TER-INT-007")
    def test_a_measure_with_no_finding_behind_it_gives_none(self) -> None:
        analysis = rework_session()
        control = session_control(analysis, rework_beyond(analysis))
        empty = replace(control, behind={})
        assert empty.firing and control_signals(empty, analysis) == ()


class TestPolicyTriggers:
    @pytest.mark.req("TER-SPC-020")
    def test_a_policy_naming_the_signal_advises_on_it(self) -> None:
        analysis = rework_session()
        channel = InMemoryInterventionChannel()
        loop = ControlLoop(
            policies(),
            InMemoryInterventionLedger(),
            FixedClock(AT),
            channel,
            limits=rework_beyond(analysis),
        )
        turn = loop.act("s", analysis)
        [advice] = channel.interventions
        assert advice.record.policy == "rework-above-limit"
        assert advice.record.detector == REWORK
        [signal] = turn.control
        assert advice.record.evidence == signal.evidence
        [message] = channel.delivered()
        assert "TER warning (rework-above-limit)" in message
        assert f"({REWORK}, confidence 1.00" in message
        assert signal.evidence[0] in message.splitlines()[-1]
        assert [s.detector for s in turn.control] == [REWORK]

    @pytest.mark.req("TER-SPC-020", "TER-INT-010")
    def test_an_ineligible_control_signal_is_ledgered_not_advised(self) -> None:
        analysis = rework_session()
        ledger, channel = InMemoryInterventionLedger(), InMemoryInterventionChannel()
        loop = ControlLoop(
            policies(eligible=()),
            ledger,
            FixedClock(AT),
            channel,
            limits=rework_beyond(analysis),
        )
        loop.act("s", analysis)
        assert channel.interventions == []
        [record] = [r for r in ledger.entries("s") if r.detector == REWORK]
        assert record.outcome is Outcome.INELIGIBLE

    @pytest.mark.req("TER-SPC-020")
    def test_without_limits_or_with_stale_ones_nothing_fires(self) -> None:
        analysis = rework_session()
        for given in (None, rework_beyond(analysis, "other")):
            ledger = InMemoryInterventionLedger()
            loop = ControlLoop(policies(), ledger, FixedClock(AT), limits=given)
            turn = loop.act("s", analysis)
            assert turn.control == ()
            assert turn.stale_limits is (given is not None)
            assert not any(r.detector == REWORK for r in ledger.entries("s"))

    @pytest.mark.req("TER-SPC-020", "TER-INT-017")
    def test_shadow_records_the_control_advice_and_delivers_nothing(self) -> None:
        analysis = rework_session()
        ledger = InMemoryInterventionLedger()
        loop = ControlLoop(
            policies(), ledger, FixedClock(AT), limits=rework_beyond(analysis)
        )
        turn = loop.act("s", analysis)
        [advised] = [r for r in turn.decisions.records if r.delivered]
        assert advised.detector == REWORK and advised.delivered_at is None


def _live(log: Any, calls: int, start: int = 0) -> str:
    """Reads repeated between shell calls, as the hook records them."""
    import json
    from pathlib import Path

    from ter import bootstrap
    from ter.adapters.driving.claude_hooks import handle_hook
    from ter.application import RecordEvent

    hooks = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"
    names = ("post_tool_use_bash", "post_tool_use_read")
    templates = [json.loads((hooks / f"{n}.json").read_text()) for n in names]
    recorder = RecordEvent(bootstrap.make_tokenizer("regex"), log)
    for n in range(start, start + calls):
        handle_hook(dict(templates[n % 2], tool_use_id=f"t{n}"), recorder)
    return str(templates[0]["session_id"])


def _confident_beyond(log: Any, session: str) -> ControlLimits:
    """Limits the live session's confident findings are above."""
    from ter import bootstrap
    from ter.application.observe import ingest_all

    analysis = ingest_all(bootstrap.make_ingest("regex"), log.events(session)).explain(
        session
    )
    return limits(
        detector_fingerprint(analysis),
        measure_limits("confident_findings", 0.0, 0.1, 0.3, None),
    )


def _confident_policies() -> PolicySet:
    signal = "control.confident_findings.beyond_limits"
    return PolicySet.from_mapping(
        {
            "schema": "ter.intervention-policies/1",
            "eligibility": {"detectors": [signal], "basis": "test"},
            "policies": [
                {
                    "id": "waste-above-limit",
                    "detectors": [signal],
                    "advice": {"kind": "warning", "message": "More waste than usual."},
                }
            ],
        }
    ).approve("Test", date(2026, 10, 12))


@pytest.mark.req("TER-SPC-020", "TER-INT-011")
def test_control_advice_still_firing_after_its_window_was_ignored() -> None:
    from ter import bootstrap
    from ter.adapters.driven.in_memory import InMemoryEventLog

    log, ledger = InMemoryEventLog(), InMemoryInterventionLedger()
    session = _live(log, 8)
    bounds = _confident_beyond(log, session)

    def turn() -> Any:
        loop = ControlLoop(
            _confident_policies(),
            ledger,
            FixedClock(AT),
            InMemoryInterventionChannel(),
            limits=bounds,
        )
        return loop.turn(session, log, lambda: bootstrap.make_ingest("regex"))

    [given] = turn().delivered
    assert given.record.detector == "control.confident_findings.beyond_limits"
    _live(log, 12, start=8)
    later = turn()
    [observed] = later.observed
    assert observed.id == given.record.id
    assert observed.observation is not None
    assert observed.observation.response.value == "ignored"


@pytest.mark.req("TER-SPC-020", "TER-INT-011")
def test_control_advice_waits_for_a_turn_with_limits() -> None:
    from ter import bootstrap
    from ter.adapters.driven.in_memory import InMemoryEventLog

    log, ledger = InMemoryEventLog(), InMemoryInterventionLedger()
    session = _live(log, 8)
    bounds = _confident_beyond(log, session)

    def turn(with_limits: ControlLimits | None) -> Any:
        loop = ControlLoop(
            _confident_policies(),
            ledger,
            FixedClock(AT),
            InMemoryInterventionChannel(),
            limits=with_limits,
        )
        return loop.turn(session, log, lambda: bootstrap.make_ingest("regex"))

    [given] = turn(bounds).delivered
    _live(log, 12, start=8)
    # Past its window, but nothing here can judge it: it is not accepted.
    assert turn(None).observed == ()
    stale = replace(bounds, detectors="0" * 12)
    assert turn(stale).observed == ()
    [observed] = turn(bounds).observed
    assert observed.id == given.record.id


@pytest.mark.req("TER-SPC-020")
def test_the_hook_advises_on_control_signals_with_limits(tmp_path: Any) -> None:
    import io
    import json

    from ter import bootstrap
    from ter.adapters.driven.event_log import JsonlEventLog
    from ter.adapters.driving.cli import main

    log = JsonlEventLog(tmp_path / "events")
    session = _live(log, 8)
    bounds = tmp_path / "limits.json"
    bounds.write_text(json.dumps(_confident_beyond(log, session).as_dict()))
    stale = tmp_path / "stale.json"
    stale_limits = _confident_beyond(log, session)
    stale.write_text(
        json.dumps({**stale_limits.as_dict(), "detectors": "another-detector-set"})
    )
    policy_file = tmp_path / "policies.json"
    policy_file.write_text(json.dumps(_confident_policies().as_dict()))
    stop = json.dumps(
        {"hook_event_name": "Stop", "session_id": session, "stop_hook_active": False}
    )

    def hook(limits_file: Any, ledger: str) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["hook", "--event-log", str(tmp_path / "events")]
            + ["--advise", str(policy_file), "--ledger", str(tmp_path / ledger)]
            + ["--limits", str(limits_file)],
            bootstrap.cli_services(),
            stdin=io.StringIO(stop),
            stdout=out,
            stderr=err,
        )
        assert code == 0
        return out.getvalue(), err.getvalue()

    out, err = hook(bounds, "ledger")
    assert err == ""
    assert "TER warning (waste-above-limit)" in json.loads(out)["systemMessage"]
    out, err = hook(stale, "stale-ledger")
    assert json.loads(out) == {}
    assert "control signals not used" in err
    out, err = hook(tmp_path / "missing.json", "missing-ledger")
    assert json.loads(out) == {} and "no advice given" in err


@pytest.mark.req("TER-SPC-020", "TER-INT-017")
def test_policy_shadow_takes_limits_and_says_when_they_are_stale(
    tmp_path: Any,
) -> None:
    import io
    import json
    from pathlib import Path

    from ter import bootstrap
    from ter.adapters.driving.cli import main

    session = Path(__file__).resolve().parents[2] / "sample_sessions"
    session = session / "example_session.jsonl"
    policy_file = tmp_path / "policies.json"
    policy_file.write_text(json.dumps(policies().as_dict()))
    stale = tmp_path / "limits.json"
    stale.write_text(
        json.dumps(
            limits(
                "another", measure_limits("rework_cycles", 0.0, 0.2, 0.5, None)
            ).as_dict()
        )
    )

    def shadow(limits_file: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["policy", "shadow", str(policy_file), str(session)]
            + ["--ledger", str(tmp_path / "ledger"), "--limits", str(limits_file)],
            bootstrap.cli_services(),
            stdout=out,
            stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    code, out, err = shadow(stale)
    assert (code, err) == (0, "")
    assert "Control signals not used for 1 session(s)" in out
    code, out, err = shadow(tmp_path / "missing.json")
    assert code == 2 and "Cannot read the control limits" in err
