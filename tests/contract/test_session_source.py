"""Contract suite for the ``SessionSource`` port.

The real Claude Code adapter and the in-memory fake run the same assertions,
so the fake cannot drift from the obligations the real adapter meets.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.in_memory import InMemorySessionSource
from ter.domain import Actor, EventKind
from ter.ports import SessionSource

from golden.corpus import CORPUS

Factory = Callable[[], tuple[SessionSource, list[str]]]


def _claude() -> tuple[SessionSource, list[str]]:
    return ClaudeCodeJsonlSource(), [str(p) for p in CORPUS.values()]


def _memory() -> tuple[SessionSource, list[str]]:
    real = ClaudeCodeJsonlSource()
    traces = {name: real.read(path) for name, path in CORPUS.items()}
    return InMemorySessionSource(traces), list(traces)


@pytest.fixture(params=[_claude, _memory], ids=["claude-code-jsonl", "in-memory"])
def source(request: pytest.FixtureRequest) -> tuple[SessionSource, list[str]]:
    factory: Factory = request.param
    return factory()


def test_satisfies_the_port_protocol(source: tuple[SessionSource, list[str]]) -> None:
    adapter, _ = source
    assert isinstance(adapter, SessionSource)
    assert adapter.format_name


@pytest.mark.req("TER-SRC-004")
def test_reading_is_deterministic(source: tuple[SessionSource, list[str]]) -> None:
    adapter, refs = source
    for ref in refs:
        assert adapter.read(ref) == adapter.read(ref)


def test_event_ids_are_unique_and_sequences_contiguous(
    source: tuple[SessionSource, list[str]],
) -> None:
    adapter, refs = source
    for ref in refs:
        trace = adapter.read(ref)
        ids = [e.id for e in trace.events]
        assert len(ids) == len(set(ids)), ref
        assert [e.sequence for e in trace.events] == list(range(len(trace.events)))
        assert trace.source_format == adapter.format_name


def test_events_belong_to_their_session_and_chain_in_order(
    source: tuple[SessionSource, list[str]],
) -> None:
    adapter, refs = source
    for ref in refs:
        trace = adapter.read(ref)
        assert trace.events, ref
        assert {e.session_id for e in trace.events} == {trace.session_id}
        assert trace.events[0].parent_id is None
        for earlier, later in zip(trace.events, trace.events[1:]):
            assert later.parent_id == earlier.id


@pytest.mark.req("TER-ANL-001")
def test_generated_events_exclude_user_input_and_tool_output(
    source: tuple[SessionSource, list[str]],
) -> None:
    adapter, refs = source
    for ref in refs:
        generated = adapter.read(ref).generated()
        assert generated
        assert all(e.actor is Actor.ASSISTANT for e in generated)
        assert not {e.kind for e in generated} & {
            EventKind.PROMPT,
            EventKind.TOOL_COMPLETED,
        }


def test_unknown_reference_raises_file_not_found(
    source: tuple[SessionSource, list[str]], tmp_path: Path
) -> None:
    adapter, _ = source
    with pytest.raises(FileNotFoundError):
        adapter.read(str(tmp_path / "missing.jsonl"))
