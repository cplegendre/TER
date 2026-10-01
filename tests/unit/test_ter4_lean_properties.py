"""Property tests for L2: traceable evidence, bounded confidence, determinism,
and live (incremental, with redeliveries) == batch."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ter4_lean_builder import FAIL, FAIL_OTHER, PASS, Script

from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.domain import AnalysisEngine, Event
from ter.domain.lean import UNCERTAIN_BELOW, FindingKind, build_a3, explain

_paths = st.sampled_from(["src/a.py", "src/b.py", "README.md"])
_outputs = st.sampled_from(["def a(): pass", "x = 1", FAIL, FAIL_OTHER, PASS, "a.py"])
_words = st.sampled_from(
    [
        "find where the parser handles arguments",
        "plan the retry decorator",
        "the parser handles arguments in main",
        "done",
    ]
)

_ops = st.one_of(
    st.tuples(st.just("prompt"), _words),
    st.tuples(st.just("think"), _words),
    st.tuples(st.just("say"), _words),
    st.tuples(st.just("read"), _paths, _outputs),
    st.tuples(st.just("edit"), _paths, st.sampled_from(["1", "2"])),
    st.tuples(st.just("write"), _paths, st.sampled_from(["a\nb\nc\n", "a\nb\nc\nd\n"])),
    st.tuples(st.just("bash"), st.sampled_from(["pytest -q", "ls src"]), _outputs),
    st.tuples(st.just("search"), st.sampled_from(["argparse", "retry"]), _outputs),
    st.tuples(st.just("todo"), _words),
    st.tuples(st.just("task"), _words, _outputs),
)


def _script(ops: list[tuple[str, ...]], timed: bool) -> Script:
    s = Script(timed=timed)
    for op in ops:
        name, *args = op
        if name == "task":
            s.task(args[0], args[0], args[1])
        elif name == "edit":
            s.edit(args[0], args[1])
        else:
            getattr(s, name)(*args)
    return s


@st.composite
def scripts(draw: st.DrawFn) -> Script:
    return _script(draw(st.lists(_ops, max_size=24)), draw(st.booleans()))


@pytest.mark.req(
    "TER-DET-001",
    "TER-ANL-020",
    "TER-ANL-021",
    "TER-LEN-002",
    "TER-FLW-001",
    "TER-GRF-002",
)
@settings(max_examples=150, deadline=None)
@given(scripts())
def test_findings_are_traceable_and_bounded(script: Script) -> None:
    a = explain(script.events, RegexTokenizer())
    ids = {e.id for e in script.events}
    for f in a.findings:
        assert f.evidence and set(f.evidence) <= ids
        assert set(f.waste_events) <= set(f.evidence)
        assert 0.0 <= f.confidence <= 1.0
        assert f.uncertain == (f.confidence < UNCERTAIN_BELOW)
        assert 0.0 <= f.share <= 1.0
        if f.kind is FindingKind.RISK:
            assert f.waste_events == () and f.tokens == 0
    assert [c.event_id for c in a.classifications] == [e.id for e in script.events]
    for c in a.classifications:
        assert 0.0 <= c.avoidable_share + c.uncertain_share <= 1.0 + 1e-9
    sc = a.scorecard
    assert sum(n for _, n in sc.activity_tokens) == sc.generated_tokens
    assert sum(n for _, n in sc.flow_tokens) == sc.generated_tokens
    for seconds in (sc.activity_seconds, sc.flow_seconds):
        assert sum(v for _, v in seconds) == pytest.approx(sc.agent_seconds, abs=1e-6)
    for value in (sc.flow_efficiency_tokens, sc.flow_efficiency_time):
        assert value is None or 0.0 <= value <= 1.0 + 1e-9
    index = {e.id: i for i, e in enumerate(script.events)}
    for edge in a.graph.edges:
        assert index[edge.target] < index[edge.source]


@pytest.mark.req("TER-LEN-008")
@settings(max_examples=60, deadline=None)
@given(scripts())
def test_explanation_is_deterministic(script: Script) -> None:
    first = explain(script.events, RegexTokenizer())
    second = explain(list(script.events), RegexTokenizer())
    assert first == second
    rendered = json.dumps(build_a3(first, ["x"]).as_dict(), sort_keys=True)
    assert rendered == json.dumps(build_a3(second, ["x"]).as_dict(), sort_keys=True)


@pytest.mark.req("TER-ANL-010")
@settings(max_examples=80, deadline=None)
@given(scripts(), st.data())
def test_live_with_redelivery_equals_batch(script: Script, data: st.DataObject) -> None:
    events: list[Event] = script.events
    engine = AnalysisEngine(RegexTokenizer())
    for n, event in enumerate(events, 1):
        engine.apply(event)
        if data.draw(st.booleans()):
            engine.apply(events[data.draw(st.integers(0, n - 1))])
    assert engine.explain() == explain(events, RegexTokenizer())
