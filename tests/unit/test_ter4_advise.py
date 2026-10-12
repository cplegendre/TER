"""L4 Advisory: live advice (TER-INT-003, TER-INT-014).

A session's detector signals meet the approved policies; each advised
decision is ledgered and delivered once, with the signal and evidence behind
it. Asking again delivers only what is new.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from ter import bootstrap
from ter.adapters.driven.in_memory import (
    FixedClock,
    InMemoryInterventionChannel,
    InMemoryInterventionLedger,
)
from ter.application.advise import decide
from ter.application.loop import advise_session
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


@pytest.mark.req("TER-INT-003")
def test_a_finding_that_gains_evidence_is_not_advised_again(
    analysis: LeanAnalysis,
) -> None:
    first = InMemoryInterventionLedger()
    advise_session(
        SID, analysis, approved(), first, InMemoryInterventionChannel(), FixedClock(AT)
    )
    # The ledger as an earlier Stop left it: the same advice given on an
    # earlier revision of the signal (fewer evidence events, another id).
    earlier = InMemoryInterventionLedger()
    for entry in first.entries(SID):
        if entry.delivered:
            entry = replace(entry, id="int-earlier", evidence=entry.evidence[:1])
        earlier.record(entry)
    channel = InMemoryInterventionChannel()
    decisions = advise_session(
        SID, analysis, approved(), earlier, channel, FixedClock(AT)
    )
    assert channel.interventions == [] and decisions.new_interventions == ()
    assert sum(e.delivered for e in earlier.entries(SID)) == 1


@pytest.mark.req("TER-INT-003")
def test_the_same_signal_under_a_revised_file_is_advised_again(
    analysis: LeanAnalysis,
) -> None:
    ledger = InMemoryInterventionLedger()
    advise_session(
        SID, analysis, approved(), ledger, InMemoryInterventionChannel(), FixedClock(AT)
    )
    content = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    content["eligibility"]["basis"] += " Revised."
    revised = PolicySet.from_mapping(content).approve("Test", date(2026, 10, 12))
    channel = InMemoryInterventionChannel()
    advise_session(SID, analysis, revised, ledger, channel, FixedClock(AT))
    assert len(channel.interventions) == 1


def test_advice_on_a_long_session_stays_well_inside_the_hook_timeout(
    tmp_path: Path,
) -> None:
    """Benchmark: a Stop with ``--advise`` replays and explains the session.

    That cost grows with the session, once per turn (point 123). On 3,000
    events (a repeated read every other call) it takes about a second here; Claude Code's default hook timeout
    is 60 seconds, and the bound leaves CI noise a wide margin.
    """
    from ter.adapters.driven.event_log import JsonlEventLog
    from ter.adapters.driving.claude_hooks import handle_hook
    from ter.application import RecordEvent

    hooks = REPO / "tests" / "fixtures" / "hooks"
    names = ("post_tool_use_bash", "post_tool_use_read")
    templates = [json.loads((hooks / f"{n}.json").read_text()) for n in names]
    log = tmp_path / "events"
    recorder = RecordEvent(bootstrap.make_tokenizer("regex"), JsonlEventLog(log))
    for n in range(1500):
        handle_hook(dict(templates[n % 2], tool_use_id=f"t{n}"), recorder)
    session = templates[0]["session_id"]
    policies = tmp_path / "policies.json"
    content = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    content.update(approved().as_dict())
    policies.write_text(json.dumps(content), encoding="utf-8")

    start = time.perf_counter()
    advice = bootstrap.advise_from_log(log, session, policies, tmp_path / "ledger")
    elapsed = time.perf_counter() - start
    assert len(JsonlEventLog(log).events(session)) == 3000
    assert advice  # a repeated read after each cooldown
    assert elapsed < 15.0, f"{elapsed:.2f} s"


def _live_session(tmp_path: Path, calls: int, start: int = 0) -> str:
    """A live event log of reads repeated between shell calls, as the hook
    records it; returns the session id."""
    from ter.adapters.driven.event_log import JsonlEventLog
    from ter.adapters.driving.claude_hooks import handle_hook
    from ter.application import RecordEvent

    hooks = REPO / "tests" / "fixtures" / "hooks"
    names = ("post_tool_use_bash", "post_tool_use_read")
    templates = [json.loads((hooks / f"{n}.json").read_text()) for n in names]
    recorder = RecordEvent(
        bootstrap.make_tokenizer("regex"), JsonlEventLog(tmp_path / "events")
    )
    for n in range(start, start + calls):
        handle_hook(dict(templates[n % 2], tool_use_id=f"t{n}"), recorder)
    return str(templates[0]["session_id"])


def _approved_file(tmp_path: Path) -> Path:
    policies = tmp_path / "policies.json"
    policies.write_text(json.dumps(approved().as_dict()), encoding="utf-8")
    return policies


@pytest.mark.req("TER-INT-011")
def test_a_later_stop_records_the_outcome_of_earlier_advice(tmp_path: Path) -> None:
    from ter.adapters.driven.intervention_ledger import JsonlInterventionLedger

    policies, ledger = _approved_file(tmp_path), tmp_path / "ledger"
    session = _live_session(tmp_path, 8)
    assert bootstrap.advise_from_log(tmp_path / "events", session, policies, ledger)
    [given] = [
        e for e in JsonlInterventionLedger(ledger).entries(session) if e.delivered
    ]
    assert given.delivered_at == 8 and given.observation is None

    _live_session(tmp_path, 6, start=8)  # 6 more tool events: too soon
    bootstrap.advise_from_log(tmp_path / "events", session, policies, ledger)
    entries = {e.id: e for e in JsonlInterventionLedger(ledger).entries(session)}
    assert entries[given.id].observation is None

    _live_session(tmp_path, 6, start=14)  # 12 after the advice
    bootstrap.advise_from_log(tmp_path / "events", session, policies, ledger)
    entries = {e.id: e for e in JsonlInterventionLedger(ledger).entries(session)}
    observation = entries[given.id].observation
    assert observation is not None and observation.tool_events == 12
    # The same file kept being re-read: the advice was ignored.
    assert observation.response.value == "ignored"
    assert observation.after["repeats_per_tool_event"] > 0


@pytest.mark.req("TER-INT-012")
def test_policy_effectiveness_reads_the_live_ledger(tmp_path: Path) -> None:
    import io

    from ter.adapters.driving.cli import main

    policies, ledger = _approved_file(tmp_path), tmp_path / "ledger"
    session = _live_session(tmp_path, 8)
    bootstrap.advise_from_log(tmp_path / "events", session, policies, ledger)
    _live_session(tmp_path, 12, start=8)
    bootstrap.advise_from_log(tmp_path / "events", session, policies, ledger)

    out, err = io.StringIO(), io.StringIO()
    report = tmp_path / "effectiveness.json"
    code = main(
        ["policy", "effectiveness", "--ledger", str(ledger), "--json", str(report)],
        bootstrap.cli_services(),
        stdout=out,
        stderr=err,
    )
    assert (code, err.getvalue()) == (0, "")
    assert "reuse-reads (" in out.getvalue() and ": unproven" in out.getvalue()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["schema"] == "ter.policy-effectiveness/1"
    [row] = [p for p in data["policies"] if p["policy"] == "reuse-reads"]
    assert row["observed"] >= 1 and row["effect"] == "unproven"


@pytest.mark.req("TER-INT-012")
def test_policy_effectiveness_needs_a_ledger_folder(tmp_path: Path) -> None:
    import io

    from ter.adapters.driving.cli import main

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["policy", "effectiveness", "--ledger", str(tmp_path / "missing")],
        bootstrap.cli_services(),
        stdout=out,
        stderr=err,
    )
    assert code == 2 and "No such ledger folder" in err.getvalue()


@pytest.mark.req("TER-INT-011")
def test_advice_under_another_policy_file_is_measured_on_its_own_detector(
    analysis: LeanAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ter.application import advise

    ledger = InMemoryInterventionLedger()
    advise_session(
        SID, analysis, approved(), ledger, InMemoryInterventionChannel(), FixedClock(AT)
    )
    [given] = [e for e in ledger.entries(SID) if e.delivered]
    ledger.record(replace(given, id="int-old", policy_version="sha256:old"))
    seen: dict[str, frozenset[str]] = {}

    def spy(
        record: Any, signals: Any, tool_events: int, detectors: Any, at: Any
    ) -> None:
        seen[record.id] = frozenset(detectors)

    monkeypatch.setattr(advise, "follow_up", spy)
    advise.follow_up_session(SID, analysis, approved(), ledger, FixedClock(AT))
    current = next(p for p in approved().policies if p.id == "reuse-reads")
    assert seen[given.id] == current.detectors
    assert seen["int-old"] == frozenset()  # the record's own detector only
