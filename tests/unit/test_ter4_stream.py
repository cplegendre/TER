"""Unit tests for the L1 incremental analysis engine (ter.domain.stream)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.domain import (
    Actor,
    AnalysisEngine,
    Event,
    EventClass,
    EventKind,
    Provenance,
    Signal,
    TokenUsage,
    ToolCall,
    ToolKind,
    analyse_batch,
    make_event_id,
)
from ter.domain.stream import canonical_arguments
from ter.ports import Tokenizer

SESSION = "s1"


def ev(
    n: int,
    kind: EventKind,
    *,
    text: str = "",
    tool: ToolKind | None = None,
    call: str | None = None,
    args: dict[str, Any] | None = None,
    usage: TokenUsage | None = None,
    session: str = SESSION,
) -> Event:
    actor = {
        EventKind.PROMPT: Actor.USER,
        EventKind.TOOL_COMPLETED: Actor.TOOL,
    }.get(kind, Actor.ASSISTANT)
    return Event(
        id=make_event_id(session, n, kind.value),
        session_id=session,
        sequence=n,
        kind=kind,
        actor=actor,
        text=text,
        provenance=Provenance("test", f"r{n}"),
        tool=None
        if tool is None
        else ToolCall(tool.value, tool, call_id=call, arguments=args or {}),
        usage=usage,
    )


def req(n: int, tool: ToolKind, call: str | None, **args: Any) -> Event:
    return ev(n, EventKind.TOOL_REQUESTED, tool=tool, call=call, args=args)


def done(n: int, tool: ToolKind, call: str | None) -> Event:
    return ev(n, EventKind.TOOL_COMPLETED, tool=tool, call=call, text="ok")


@pytest.fixture
def engine() -> AnalysisEngine:
    return AnalysisEngine(RegexTokenizer())


def test_regex_tokenizer_satisfies_the_engine_and_port_protocols() -> None:
    assert isinstance(RegexTokenizer(), Tokenizer)
    AnalysisEngine(RegexTokenizer())


def test_empty_engine_reports_nothing(engine: AnalysisEngine) -> None:
    report = engine.snapshot()
    assert report.session_id is None
    assert report.total_events == 0
    assert report.by_kind == report.by_tool == report.timeline == ()
    assert report.usage == TokenUsage()
    assert report.tokenizer == "regex-v1" and report.tokens_exact is False
    assert len(engine) == 0


def test_counts_classes_tokens_and_usage(engine: AnalysisEngine) -> None:
    engine.apply(ev(0, EventKind.PROMPT, text="fix the parser"))
    engine.apply(
        ev(
            1,
            EventKind.REASONING,
            text="look at it",
            usage=TokenUsage(10, 5, 2, 100),
        )
    )
    engine.apply(req(2, ToolKind.FS_READ, "c1", file_path="a.py"))
    engine.apply(done(3, ToolKind.FS_READ, "c1"))
    engine.apply(ev(4, EventKind.RESPONSE, text="done", usage=TokenUsage(1, 1)))
    report = engine.snapshot()

    assert report.session_id == SESSION
    assert report.total_events == 5 == len(engine)
    assert report.count(EventKind.PROMPT) == 1
    assert report.count(EventKind.TOOL_REQUESTED) == 1
    assert report.count(ToolKind.FS_READ) == 1
    assert report.count(ToolKind.EXEC_SHELL) == 0
    assert (report.generated_events, report.user_events, report.tool_events) == (
        3,
        1,
        1,
    )
    assert report.tokens(EventClass.USER) == 3
    assert report.tokens(EventClass.TOOL) == 1
    assert report.usage == TokenUsage(11, 6, 2, 100)
    assert [r.index for r in report.timeline] == [0, 1, 2, 3, 4]
    assert report.timeline[1].output_tokens == 5
    assert report.timeline[2].tool_kind is ToolKind.FS_READ
    assert report.timeline[0].native_name is None


@pytest.mark.req("TER-OBS-004")
def test_redelivered_event_is_discarded_without_changing_state(
    engine: AnalysisEngine,
) -> None:
    first = req(0, ToolKind.FS_READ, "c1", file_path="a.py")
    assert engine.apply(first).accepted
    before = engine.snapshot()
    again = engine.apply(first)
    assert not again.accepted
    assert again.raised == (Signal.DUPLICATE_EVENT,)
    assert Signal.DUPLICATE_EVENT in again
    assert engine.snapshot() == before
    assert first.id in engine


@pytest.mark.req("TER-OBS-004")
def test_identity_not_content_decides_redelivery(engine: AnalysisEngine) -> None:
    first = ev(0, EventKind.PROMPT, text="hello")
    engine.apply(first)
    before = engine.snapshot()
    # Same id, different content: still the recorded event, still discarded.
    assert not engine.apply(replace(first, text="something else")).accepted
    assert engine.snapshot() == before


def test_duplicate_tool_calls_use_canonical_arguments(engine: AnalysisEngine) -> None:
    engine.apply(req(0, ToolKind.FS_SEARCH, "c1", pattern="x", path="src"))
    signals = engine.apply(req(1, ToolKind.FS_SEARCH, "c2", path="src", pattern="x"))
    other = engine.apply(req(2, ToolKind.FS_SEARCH, "c3", pattern="y", path="src"))
    report = engine.snapshot()
    assert Signal.DUPLICATE_TOOL_CALL in signals
    assert Signal.DUPLICATE_TOOL_CALL not in other
    assert report.duplicate_tool_calls == (signals.event_id,)


def test_same_arguments_different_tool_kind_is_not_a_duplicate(
    engine: AnalysisEngine,
) -> None:
    engine.apply(req(0, ToolKind.FS_READ, "c1", path="a"))
    assert not engine.apply(req(1, ToolKind.FS_SEARCH, "c2", path="a")).raised


def test_repeated_reads_by_path(engine: AnalysisEngine) -> None:
    engine.apply(req(0, ToolKind.FS_READ, "c1", file_path="a.py"))
    second = engine.apply(req(1, ToolKind.FS_READ, "c2", file_path="a.py", limit=5))
    engine.apply(req(2, ToolKind.FS_READ, "c3", notebook_path="n.ipynb"))
    engine.apply(req(3, ToolKind.FS_READ, "c4", file_path="a.py", offset=9))
    engine.apply(req(4, ToolKind.FS_READ, "c5"))  # no path: not counted
    report = engine.snapshot()
    assert Signal.REPEATED_READ in second
    assert Signal.DUPLICATE_TOOL_CALL not in second
    assert report.repeated_reads == (("a.py", 3),)
    assert report.repeated_read_count == 2


def test_open_requests_and_orphan_results(engine: AnalysisEngine) -> None:
    a = engine.apply(req(0, ToolKind.FS_READ, "c1", file_path="a"))
    b = engine.apply(req(1, ToolKind.EXEC_SHELL, "c2", command="ls"))
    c = engine.apply(req(2, ToolKind.PLAN, None))
    engine.apply(done(3, ToolKind.FS_READ, "c1"))
    engine.apply(done(4, ToolKind.FS_READ, "c1"))  # second result: not an orphan
    orphan = engine.apply(done(5, ToolKind.OTHER, "never-requested"))
    no_id = engine.apply(done(6, ToolKind.OTHER, None))
    report = engine.snapshot()
    assert report.open_requests == (b.event_id, c.event_id)
    assert a.event_id not in report.open_requests
    assert report.orphan_results == (orphan.event_id, no_id.event_id)
    assert Signal.ORPHAN_RESULT in orphan


def test_edits_without_validation(engine: AnalysisEngine) -> None:
    first = engine.apply(req(0, ToolKind.FS_EDIT, "c1", file_path="a"))
    second = engine.apply(req(1, ToolKind.FS_WRITE, "c2", file_path="b"))
    engine.apply(req(2, ToolKind.FS_EDIT, "c3", file_path="c"))
    assert Signal.UNVALIDATED_EDIT not in first
    assert Signal.UNVALIDATED_EDIT in second
    assert engine.snapshot().edits_since_validation == 3
    engine.apply(req(3, ToolKind.EXEC_SHELL, "c4", command="pytest"))
    engine.apply(req(4, ToolKind.FS_EDIT, "c5", file_path="d"))
    report = engine.snapshot()
    assert report.edits_since_validation == 1
    assert report.peak_edits_without_validation == 3


def test_tool_request_without_tool_payload_is_counted_but_not_tracked(
    engine: AnalysisEngine,
) -> None:
    engine.apply(ev(0, EventKind.TOOL_REQUESTED, text="?"))
    report = engine.snapshot()
    assert report.count(EventKind.TOOL_REQUESTED) == 1
    assert report.by_tool == () and report.open_requests == ()


def test_events_from_another_session_are_rejected(engine: AnalysisEngine) -> None:
    engine.apply(ev(0, EventKind.PROMPT, text="a"))
    before = engine.snapshot()
    with pytest.raises(ValueError, match="belongs to session"):
        engine.apply(ev(1, EventKind.PROMPT, text="b", session="other"))
    assert engine.snapshot() == before


@pytest.mark.req("TER-ANL-010")
def test_batch_is_the_fold_of_apply() -> None:
    events = [
        ev(0, EventKind.PROMPT, text="go"),
        req(1, ToolKind.FS_READ, "c1", file_path="a"),
        done(2, ToolKind.FS_READ, "c1"),
        req(3, ToolKind.FS_READ, "c2", file_path="a"),
    ]
    engine = AnalysisEngine(RegexTokenizer())
    for event in events:
        engine.apply(event)
    assert engine.snapshot() == analyse_batch(events, RegexTokenizer())


def test_report_as_dict_is_json_ready() -> None:
    import json

    events = [
        ev(0, EventKind.PROMPT, text="go"),
        req(1, ToolKind.FS_READ, "c1", file_path="a"),
        req(2, ToolKind.FS_READ, "c2", file_path="a"),
        done(3, ToolKind.OTHER, "zz"),
    ]
    data = analyse_batch(events, RegexTokenizer()).as_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["by_tool"] == {"fs.read": 2}
    assert data["repeated_reads"] == {"a": 2}
    assert data["timeline"][2][-1] == ["tool.duplicate_call", "fs.repeated_read"]


def test_canonical_arguments_ignore_key_order_and_tolerate_non_json() -> None:
    assert canonical_arguments({"b": 1, "a": 2}) == canonical_arguments(
        {"a": 2, "b": 1}
    )
    assert canonical_arguments({"x": {1, 2}.__class__}) == '{"x":"<class \'set\'>"}'


def test_event_class_partition_covers_every_kind() -> None:
    classes = {kind: EventClass.of(kind) for kind in EventKind}
    assert classes[EventKind.PROMPT] is EventClass.USER
    assert classes[EventKind.TOOL_COMPLETED] is EventClass.TOOL
    assert {k for k, c in classes.items() if c is EventClass.GENERATED} == {
        k for k in EventKind if k.is_generated
    }
