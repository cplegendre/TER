"""Every reported metric is recomputed from the recorded events alone.

TER-EXP-001: a session is read from its source and reported (the L1 stream
report and the L2 A3, cost included); its events are then written to a
``ter.event`` JSONL log, and the log is read back by a fresh log adapter with
nothing else: no source file, no trace, no tokenizer state. Folding the
reloaded events reproduces every reported figure exactly.

Two things in a report are not folded from the session's activity: the TER 3
ratio (computed by TER 3 from the source transcript) and the outcome verdict
(judged from outcome evidence beside the analysis). TER-EXP-001 leaves them
out; TER-EXP-002 records both as events of the session (``metric.recorded``,
``verdict.recorded``), and the tests at the end of this module recompute
them, and every other figure, from the reloaded log alone. The source's ``usage_limits`` qualify figures rather than being
figures, and come from the source, so they are left out too. The price book
is reference data shared by every session (TER-ANL-040), not session input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.gare import GareRunSource
from ter.adapters.driven.junit import JUnitOutcomeSource
from ter.adapters.driven.pricing import default_price_book
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import AnalyseTrace, ExplainedSession, ExplainSession
from ter.application.record import RecordMeasures, explain_recorded
from ter.bootstrap import make_ter_scorer
from ter.domain import AnalysisEngine, EventKind, analyse_batch, explain_batch
from ter.domain.events import Event
from ter.domain.lean import build_a3
from ter.ports import SessionSource

from golden.corpus import CORPUS, REPO_ROOT

GARE = REPO_ROOT / "tests" / "fixtures" / "gare"

#: Report keys that are not recomputed from events (see the module docstring).
NOT_FROM_EVENTS = ("usage_limits", "outcome")

SESSIONS: dict[str, tuple[SessionSource, Path]] = {
    **{name: (ClaudeCodeJsonlSource(), path) for name, path in CORPUS.items()},
    "gare-failover-run": (GareRunSource(), GARE / "failover-run"),
    "gare-repair-mission": (GareRunSource(), GARE / "repair-mission"),
    "gare-real-c2ffdf8f1b09": (GareRunSource(), GARE / "runs" / "c2ffdf8f1b09"),
}


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k not in NOT_FROM_EVENTS}


@pytest.mark.req("TER-EXP-001")
@pytest.mark.parametrize("name", sorted(SESSIONS))
def test_reports_recompute_from_the_recorded_events_alone(
    name: str, tmp_path: Path
) -> None:
    source, ref = SESSIONS[name]
    prices = default_price_book()
    reported = ExplainSession(source, RegexTokenizer(), prices=prices)(ref)
    stream = AnalyseTrace(source, RegexTokenizer())(ref)
    session_id = reported.trace.session_id

    log = JsonlEventLog(tmp_path / "log")
    for event in reported.trace.events:
        log.append(event)
    events = JsonlEventLog(tmp_path / "log").events(session_id)
    assert events == reported.trace.events

    analysis = explain_batch(events, RegexTokenizer())
    intents = [e.text for e in events if e.kind is EventKind.PROMPT]
    a3 = build_a3(analysis, intents, prices=prices)

    assert _metrics(a3.as_dict()) == _metrics(reported.a3.as_dict())
    assert analysis.as_dict() == reported.analysis.as_dict()
    assert _metrics(analyse_batch(events, RegexTokenizer()).as_dict()) == _metrics(
        stream.as_dict()
    )
    # The cost is part of what was recomputed, and dated by the events.
    assert a3.cost is not None and a3.cost == reported.a3.cost


# --- TER-EXP-002: the TER 3 ratio and the outcome verdict as recorded events ---

OUTCOME = REPO_ROOT / "tests" / "fixtures" / "outcome" / "pytest-junit.xml"


def _record_and_reload(reported: ExplainedSession, tmp_path: Path) -> tuple[Event, ...]:
    """Record a reported session and its measures, then read the log back
    with a fresh adapter: nothing else survives."""
    RecordMeasures(JsonlEventLog(tmp_path / "log"))(reported)
    return JsonlEventLog(tmp_path / "log").events(reported.trace.session_id)


def _figures(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k != "usage_limits"}


@pytest.mark.req("TER-EXP-002")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_the_ter3_ratio_and_verdict_recompute_from_recorded_events(
    name: str, tmp_path: Path
) -> None:
    prices = default_price_book()
    reported = ExplainSession(
        ClaudeCodeJsonlSource(),
        RegexTokenizer(),
        make_ter_scorer("offline"),
        JUnitOutcomeSource(),
        prices,
    )(CORPUS[name], OUTCOME)
    assert reported.analysis.scorecard.ter is not None
    assert reported.outcome is not None

    events = _record_and_reload(reported, tmp_path)
    # The session's events, then exactly two records after the last one.
    assert events[: len(reported.trace.events)] == reported.trace.events
    records = events[len(reported.trace.events) :]
    assert [e.kind for e in records] == [
        EventKind.METRIC_RECORDED,
        EventKind.VERDICT_RECORDED,
    ]
    last = reported.trace.events[-1].sequence
    assert [e.sequence for e in records] == [last + 1, last + 2]

    rebuilt = explain_recorded(events, RegexTokenizer(), prices)
    # Every figure, the TER 3 ratio and the verdict included. Only the
    # source's usage limits are the source's, not the events'.
    assert rebuilt.analysis.as_dict() == reported.analysis.as_dict()
    assert rebuilt.analysis.scorecard.ter == reported.analysis.scorecard.ter
    assert rebuilt.outcome == reported.outcome
    assert _figures(rebuilt.a3.as_dict()) == _figures(reported.a3.as_dict())
    # The records are no activity: the L1 report is unchanged by them.
    assert (
        analyse_batch(events, RegexTokenizer()).as_dict()
        == analyse_batch(reported.trace.events, RegexTokenizer()).as_dict()
    )


@pytest.mark.req("TER-EXP-002")
def test_recording_twice_appends_nothing_new(tmp_path: Path) -> None:
    reported = ExplainSession(
        ClaudeCodeJsonlSource(),
        RegexTokenizer(),
        make_ter_scorer("offline"),
        JUnitOutcomeSource(),
    )(CORPUS["example_session"], OUTCOME)
    log = JsonlEventLog(tmp_path / "log")
    first = RecordMeasures(log)(reported)
    again = RecordMeasures(log)(reported)
    assert first == again
    events = log.events(reported.trace.session_id)
    assert len(events) == len(reported.trace.events) + 2
    # Applied twice, a record still counts once (TER-OBS-004).
    engine = AnalysisEngine(RegexTokenizer())
    engine.apply_all([*events, *first])
    assert engine.explain().as_dict() == reported.analysis.as_dict()


@pytest.mark.req("TER-EXP-002")
def test_a_session_without_measures_records_none(tmp_path: Path) -> None:
    reported = ExplainSession(ClaudeCodeJsonlSource(), RegexTokenizer())(
        CORPUS["example_session"]
    )
    assert RecordMeasures(JsonlEventLog(tmp_path / "log"))(reported) == ()
    events = JsonlEventLog(tmp_path / "log").events(reported.trace.session_id)
    rebuilt = explain_recorded(events, RegexTokenizer())
    assert rebuilt.analysis.scorecard.ter is None and rebuilt.outcome is None
    assert rebuilt.analysis.as_dict() == reported.analysis.as_dict()
