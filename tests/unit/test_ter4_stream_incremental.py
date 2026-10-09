"""Applying one event never reprocesses earlier ones (TER-ANL-011).

Operation counts, not timings: every event is an instrumented ``Event`` that
logs each attribute read, and the tokenizer logs every text it counts. While
event N is applied, the engine (L1 counters and the L2 step log) must read
event N and no other event, and tokenize event N's text exactly once, however
long the session already is. A re-delivered id is discarded after reading its
id alone, so applying it costs nothing more and changes nothing.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.domain import AnalysisEngine, Event

from tests.golden.corpus import CORPUS

#: Ids of the events whose attributes were read, in order.
READS: list[str] = []
#: The attribute names read, in the same order.
FIELDS: list[str] = []


class Watched(Event):
    """An ``Event`` that records every read of its fields."""

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("__"):
            READS.append(object.__getattribute__(self, "id"))
            FIELDS.append(name)
        return object.__getattribute__(self, name)


class CountingTokenizer:
    """The regex tokenizer, logging every text it is asked to count."""

    def __init__(self) -> None:
        self._inner = RegexTokenizer()
        self.name = self._inner.name
        self.exact = self._inner.exact
        self.texts: list[str] = []

    def count(self, text: str) -> int:
        self.texts.append(text)
        return self._inner.count(text)


def _long_session(name: str, copies: int) -> list[Watched]:
    """The corpus session repeated ``copies`` times, with fresh ids."""
    trace = ClaudeCodeJsonlSource().read(CORPUS[name])
    out: list[Watched] = []
    for copy in range(copies):
        for event in trace.events:
            values = {f.name: getattr(event, f.name) for f in fields(Event)}
            values["id"] = f"{event.id}-{copy}"
            values["sequence"] = len(out)
            out.append(Watched(**values))
    return out


@pytest.mark.req("TER-ANL-011")
@pytest.mark.parametrize("name", ["lean_mix", "rework_loop", "duplicate_exploration"])
def test_applying_event_n_reads_only_event_n(name: str) -> None:
    events = _long_session(name, copies=4)
    tokenizer = CountingTokenizer()
    engine = AnalysisEngine(tokenizer)
    for event in events:
        READS.clear()
        tokenizer.texts.clear()
        assert engine.apply(event).accepted
        assert set(READS) == {event.id}, "apply read an earlier event"
        assert tokenizer.texts == [event.text]


@pytest.mark.req("TER-ANL-011")
def test_the_cost_of_one_apply_does_not_grow_with_the_session() -> None:
    """The same event costs the same field reads at the start of a session
    and after hundreds of earlier events."""
    events = _long_session("lean_mix", copies=12)
    per_copy = len(events) // 12
    engine = AnalysisEngine(CountingTokenizer())
    reads: list[int] = []
    for event in events:
        READS.clear()
        engine.apply(event)
        reads.append(len(READS))
    copies = [reads[i : i + per_copy] for i in range(0, len(reads), per_copy)]
    # Copies differ only in their ids. The first copy takes branches the rest
    # do not (a call is new, not a repeat), so compare from the second on: a
    # fold that never looks back reads exactly as much of each event in the
    # twelfth copy, after eleven copies of history, as in the second.
    assert all(copy == copies[1] for copy in copies[2:])


@pytest.mark.req("TER-ANL-011", "TER-OBS-004")
def test_a_redelivered_event_reads_only_its_id_and_changes_nothing() -> None:
    events = _long_session("rework_loop", copies=2)
    tokenizer = CountingTokenizer()
    engine = AnalysisEngine(tokenizer)
    engine.apply_all(events)
    before = (engine.snapshot(), engine.explain())
    for event in reversed(events):
        READS.clear()
        FIELDS.clear()
        tokenizer.texts.clear()
        signals = engine.apply(event)
        assert not signals.accepted
        # Only its id, to find it already applied: no text, tool or usage.
        assert set(READS) == {event.id} and set(FIELDS) == {"id"}
        assert tokenizer.texts == []
    assert (engine.snapshot(), engine.explain()) == before
