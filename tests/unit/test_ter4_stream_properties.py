"""Property tests for the L1 engine: idempotency, order of redeliveries, sums."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.domain import (
    Actor,
    AnalysisEngine,
    Event,
    EventKind,
    Provenance,
    StreamReport,
    TokenUsage,
    ToolCall,
    ToolKind,
    analyse_batch,
    make_event_id,
)

_ACTORS = {
    EventKind.PROMPT: Actor.USER,
    EventKind.TOOL_COMPLETED: Actor.TOOL,
}

# Small alphabets so collisions (duplicate calls, shared paths, paired call
# ids) are common rather than vanishingly rare.
_paths = st.sampled_from(["a.py", "b.py", "src/c.py"])
_args = st.fixed_dictionaries(
    {},
    optional={
        "file_path": _paths,
        "command": st.sampled_from(["pytest", "ls"]),
        "limit": st.integers(0, 2),
    },
)
_call_ids = st.one_of(st.none(), st.sampled_from(["c1", "c2", "c3", "c4"]))
_usage = st.one_of(
    st.none(),
    st.builds(
        TokenUsage,
        st.integers(0, 500),
        st.integers(0, 500),
        st.integers(0, 50),
        st.integers(0, 5000),
    ),
)


@st.composite
def _event_spec(draw: st.DrawFn) -> dict[str, Any]:
    kind = draw(st.sampled_from(list(EventKind)))
    spec: dict[str, Any] = {
        "kind": kind,
        "text": draw(st.text(max_size=30)),
        "usage": draw(_usage),
        "tool": None,
    }
    if kind in (EventKind.TOOL_REQUESTED, EventKind.TOOL_COMPLETED):
        spec["tool"] = (
            draw(st.sampled_from(list(ToolKind))),
            draw(_call_ids),
            draw(_args) if kind is EventKind.TOOL_REQUESTED else {},
        )
    return spec


def _build(index: int, spec: dict[str, Any]) -> Event:
    kind: EventKind = spec["kind"]
    tool = None
    if spec["tool"] is not None:
        tool_kind, call, args = spec["tool"]
        tool = ToolCall(tool_kind.value, tool_kind, call_id=call, arguments=args)
    return Event(
        id=make_event_id("prop", index),
        session_id="prop",
        sequence=index,
        kind=kind,
        actor=_ACTORS.get(kind, Actor.ASSISTANT),
        text=spec["text"],
        provenance=Provenance("prop", str(index)),
        tool=tool,
        usage=spec["usage"],
    )


streams = st.lists(_event_spec(), max_size=40).map(
    lambda specs: [_build(i, s) for i, s in enumerate(specs)]
)


def _batch(events: list[Event]) -> StreamReport:
    return analyse_batch(events, RegexTokenizer())


@st.composite
def _with_redeliveries(draw: st.DrawFn) -> tuple[list[Event], list[Event]]:
    """A stream, and the same stream with copies re-delivered later on."""
    events = draw(streams)
    delivered = list(events)
    for _ in range(draw(st.integers(0, 15)) if events else 0):
        original = draw(st.integers(0, len(events) - 1))
        first_at = delivered.index(events[original])
        at = draw(st.integers(first_at + 1, len(delivered)))
        delivered.insert(at, events[original])
    return events, delivered


@pytest.mark.req("TER-OBS-004")
@settings(max_examples=150, deadline=None)
@given(_with_redeliveries())
def test_redelivery_changes_nothing(pair: tuple[list[Event], list[Event]]) -> None:
    events, delivered = pair
    assert _batch(delivered) == _batch(events)


def _place_copies(events: list[Event], copies: list[int], rng: Any) -> list[Event]:
    """Insert re-deliveries of ``events[i]`` for each ``i`` in ``copies``,
    each at a random position after that event's first delivery."""
    delivered = list(events)
    for original in copies:
        first_at = delivered.index(events[original])
        delivered.insert(rng.randint(first_at + 1, len(delivered)), events[original])
    return delivered


@pytest.mark.req("TER-OBS-004")
@settings(max_examples=100, deadline=None)
@given(
    streams.filter(bool),
    st.lists(st.integers(0, 39), max_size=15),
    st.randoms(use_true_random=False),
)
def test_reordering_duplicated_deliveries_changes_nothing(
    events: list[Event], picks: list[int], rng: Any
) -> None:
    copies = [i % len(events) for i in picks]
    first = _place_copies(events, copies, rng)
    rng.shuffle(copies)
    second = _place_copies(events, copies, rng)
    assert _batch(first) == _batch(second) == _batch(events)


@pytest.mark.req("TER-ANL-010")
@settings(max_examples=150, deadline=None)
@given(_with_redeliveries())
def test_incremental_equals_batch(pair: tuple[list[Event], list[Event]]) -> None:
    _, delivered = pair
    engine = AnalysisEngine(RegexTokenizer())
    for event in delivered:
        engine.apply(event)
        # A snapshot mid-stream must not disturb what follows.
        engine.snapshot()
    assert engine.snapshot() == _batch(delivered)


@settings(max_examples=150, deadline=None)
@given(streams)
def test_counts_are_non_negative_and_partition_the_total(events: list[Event]) -> None:
    report = _batch(events)
    counts = [n for _, n in report.by_kind + report.by_tool + report.by_class]
    counts += [n for _, n in report.tokens_by_class + report.repeated_reads]
    counts += [
        report.edits_since_validation,
        report.peak_edits_without_validation,
        report.usage.input_tokens,
        report.usage.output_tokens,
        report.usage.cache_creation_tokens,
        report.usage.cache_read_tokens,
    ]
    assert all(n >= 0 for n in counts)
    assert (
        report.generated_events
        + report.user_events
        + report.tool_events
        + report.lifecycle_events
        == report.total_events
        == sum(n for _, n in report.by_kind)
        == len(report.timeline)
        # A measure TER recorded about the session is no activity (TER-EXP-002).
        == len([e for e in events if not e.kind.is_record])
    )
    assert report.edits_since_validation <= report.peak_edits_without_validation
    assert sum(n for _, n in report.by_tool) <= report.count(EventKind.TOOL_REQUESTED)
    assert len(report.open_requests) <= report.count(EventKind.TOOL_REQUESTED)
    assert len(report.orphan_results) <= report.count(EventKind.TOOL_COMPLETED)
    assert set(report.duplicate_tool_calls) <= {e.id for e in events}
