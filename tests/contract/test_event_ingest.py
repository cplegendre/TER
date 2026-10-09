"""Contract suite for the ``EventIngest`` driving port.

Run against the ``ObserveEvent`` use case with no log, with an in-memory log
and with a JSONL log, and against the append-only ``RecordEvent`` a hook
process uses, so every live configuration meets the same obligations.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.in_memory import InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import ObserveEvent, RecordEvent
from ter.domain import Event, SessionTrace, analyse_batch, explain_batch
from ter.ports import EventIngest

from golden.corpus import CORPUS

Factory = Callable[[Path], EventIngest]

FACTORIES: dict[str, Factory] = {
    "no-log": lambda p: ObserveEvent(RegexTokenizer()),
    "in-memory-log": lambda p: ObserveEvent(RegexTokenizer(), InMemoryEventLog()),
    "jsonl-log": lambda p: ObserveEvent(RegexTokenizer(), JsonlEventLog(p)),
    "record-only": lambda p: RecordEvent(RegexTokenizer(), JsonlEventLog(p)),
}


@pytest.fixture(params=sorted(FACTORIES))
def ingest(request: pytest.FixtureRequest, tmp_path: Path) -> EventIngest:
    return FACTORIES[request.param](tmp_path)


@pytest.fixture(scope="module")
def traces() -> list[SessionTrace]:
    return [ClaudeCodeJsonlSource().read(p) for p in CORPUS.values()]


def test_satisfies_the_port_protocol(ingest: EventIngest) -> None:
    assert isinstance(ingest, EventIngest)


@pytest.mark.req("TER-ANL-010")
def test_live_report_equals_batch_report(
    ingest: EventIngest, traces: list[SessionTrace]
) -> None:
    for trace in traces:
        for event in trace.events:
            assert ingest.apply(event).accepted
        expected = analyse_batch(trace.events, RegexTokenizer())
        assert ingest.report(trace.session_id) == expected


@pytest.mark.req("TER-ANL-010")
def test_live_explanation_equals_batch_explanation(
    ingest: EventIngest, traces: list[SessionTrace]
) -> None:
    for trace in traces:
        for event in trace.events:
            ingest.apply(event)
        expected = explain_batch(trace.events, RegexTokenizer())
        assert ingest.explain(trace.session_id) == expected


@pytest.mark.req("TER-OBS-004")
def test_redelivery_changes_nothing(
    ingest: EventIngest, traces: list[SessionTrace]
) -> None:
    trace = traces[0]
    for event in trace.events:
        ingest.apply(event)
    before = ingest.report(trace.session_id)
    for event in reversed(trace.events):
        assert not ingest.apply(event).accepted
    assert ingest.report(trace.session_id) == before


def test_sessions_are_analysed_separately(
    ingest: EventIngest, traces: list[SessionTrace]
) -> None:
    first, second = traces[0], traces[1]
    # Interleave two sessions event by event.
    for a, b in zip(first.events, second.events, strict=False):
        ingest.apply(a)
        ingest.apply(b)
    for trace in (first, second):
        for event in trace.events:
            ingest.apply(event)
        assert ingest.report(trace.session_id) == analyse_batch(
            trace.events, RegexTokenizer()
        )


def test_unknown_session_reports_empty(ingest: EventIngest) -> None:
    report = ingest.report("never-seen")
    assert report.total_events == 0 and report.session_id is None


def test_a_new_process_resumes_from_the_log(
    tmp_path: Path, traces: list[SessionTrace]
) -> None:
    """Live mode runs one hook process per event: state must come from the log."""
    trace = traces[0]
    half = len(trace.events) // 2
    first = ObserveEvent(RegexTokenizer(), JsonlEventLog(tmp_path))
    for event in trace.events[:half]:
        first.apply(event)
    for event in trace.events:
        # Each "process" is a fresh use case over the same directory.
        ObserveEvent(RegexTokenizer(), JsonlEventLog(tmp_path)).apply(event)
    log = JsonlEventLog(tmp_path)
    assert log.events(trace.session_id) == trace.events
    fresh = ObserveEvent(RegexTokenizer(), log)
    assert fresh.report(trace.session_id) == analyse_batch(
        trace.events, RegexTokenizer()
    )


@pytest.mark.req("TER-OBS-004")
def test_hook_processes_that_record_again_leave_the_report_unchanged(
    tmp_path: Path, traces: list[SessionTrace]
) -> None:
    """One process per hook: repeats across processes are dropped on analysis."""
    trace = traces[0]
    for event in trace.events + tuple(reversed(trace.events)):
        RecordEvent(RegexTokenizer(), JsonlEventLog(tmp_path)).apply(event)
    fresh = RecordEvent(RegexTokenizer(), JsonlEventLog(tmp_path))
    assert fresh.report(trace.session_id) == analyse_batch(
        trace.events, RegexTokenizer()
    )


class _FailingOnce(InMemoryEventLog):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    def append(self, event: Event) -> None:
        if self.failures:
            self.failures -= 1
            raise OSError("disk full")
        super().append(event)


def test_an_event_whose_append_failed_can_be_delivered_again(
    traces: list[SessionTrace],
) -> None:
    trace = traces[0]
    log = _FailingOnce()
    ingest = ObserveEvent(RegexTokenizer(), log)
    first = trace.events[0]
    with pytest.raises(OSError, match="disk full"):
        ingest.apply(first)
    assert ingest.report(trace.session_id).total_events == 0
    assert ingest.apply(first).accepted
    assert log.events(trace.session_id) == (first,)
