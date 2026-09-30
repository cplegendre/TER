"""Live analysis equals static analysis on every golden corpus session.

"Live" replays a session's events one at a time, as hooks would deliver them,
taking a snapshot after every event. "Static" analyses the whole trace at
once. TER-ANL-010 requires the final reports to be identical, and every
intermediate live snapshot must equal the batch analysis of the prefix.
"""

from __future__ import annotations

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.in_memory import InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import AnalyseEventLog, AnalyseTrace, ObserveEvent
from ter.domain import AnalysisEngine, analyse_batch, explain_batch

from golden.corpus import CORPUS


@pytest.mark.req("TER-ANL-010")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_replay_one_by_one_equals_batch(name: str) -> None:
    trace = ClaudeCodeJsonlSource().read(CORPUS[name])
    static = AnalyseTrace(ClaudeCodeJsonlSource(), RegexTokenizer())(CORPUS[name])

    engine = AnalysisEngine(RegexTokenizer())
    for n, event in enumerate(trace.events, 1):
        engine.apply(event)
        if n in (1, len(trace.events) // 2):
            assert engine.snapshot() == analyse_batch(
                trace.events[:n], RegexTokenizer()
            )
    assert engine.snapshot() == static
    assert static.total_events == len(trace.events)


@pytest.mark.req("TER-ANL-010", "TER-OBS-004")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_live_with_redelivery_through_the_log_equals_batch(name: str) -> None:
    trace = ClaudeCodeJsonlSource().read(CORPUS[name])
    log = InMemoryEventLog()
    live = ObserveEvent(RegexTokenizer(), log)
    for event in trace.events:
        live.apply(event)
        live.apply(event)  # every hook fires twice
    static = analyse_batch(trace.events, RegexTokenizer())
    assert live.report(trace.session_id) == static
    assert AnalyseEventLog(log, RegexTokenizer())(trace.session_id) == static


@pytest.mark.req("TER-ANL-010")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_live_explanation_equals_batch_explanation(name: str) -> None:
    """L2: findings, value stream, scorecard and evidence graph from the live
    fold (with every event delivered twice) equal the batch explanation, and
    every prefix explained live equals the batch explanation of that prefix."""
    trace = ClaudeCodeJsonlSource().read(CORPUS[name])
    engine = AnalysisEngine(RegexTokenizer())
    for n, event in enumerate(trace.events, 1):
        engine.apply(event)
        engine.apply(event)
        if n == len(trace.events) // 2:
            assert engine.explain() == explain_batch(trace.events[:n], RegexTokenizer())
    batch = explain_batch(trace.events, RegexTokenizer())
    assert engine.explain() == batch
    assert engine.explain().as_dict() == batch.as_dict()
