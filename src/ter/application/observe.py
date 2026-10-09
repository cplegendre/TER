"""L1 use cases: observe a session live, or analyse a recorded one.

Both go through the same :class:`~ter.domain.stream.AnalysisEngine`, so the
live report and the batch report of one event stream are the same value.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ..domain.events import Event, EventId
from ..domain.stream import (
    AnalysisEngine,
    Signal,
    Signals,
    StreamReport,
    analyse_batch,
)
from ..ports.driven import EventLog, SessionSource, Tokenizer

__all__ = ["AnalyseEventLog", "AnalyseTrace", "ObserveEvent", "RecordEvent"]


class ObserveEvent:
    """The :class:`~ter.ports.driving.EventIngest` use case.

    Keeps one engine per session. When an :class:`EventLog` is given, the
    first event of a session replays that session's log into a fresh engine
    (so a short-lived hook process sees the whole session), and every event
    the engine accepts is appended to the log. Re-delivered events are
    neither re-applied nor re-appended. A new event is appended before the
    engine applies it, so an append that fails leaves the engine unchanged and
    the same event can be delivered again.
    """

    def __init__(
        self,
        tokenizer: Tokenizer,
        log: EventLog | None = None,
        engine_factory: Callable[[Tokenizer], AnalysisEngine] = AnalysisEngine,
    ) -> None:
        self._tokenizer = tokenizer
        self._log = log
        self._engine_factory = engine_factory
        self._engines: dict[str, AnalysisEngine] = {}

    def apply(self, event: Event) -> Signals:
        engine = self._engine(event.session_id)
        if self._log is not None and event.id not in engine:
            self._log.append(event)
        return engine.apply(event)

    def report(self, session_id: str) -> StreamReport:
        return self._engine(session_id).snapshot()

    def _engine(self, session_id: str) -> AnalysisEngine:
        engine = self._engines.get(session_id)
        if engine is None:
            engine = self._engine_factory(self._tokenizer)
            if self._log is not None:
                engine.apply_all(self._log.events(session_id))
            self._engines[session_id] = engine
        return engine


class RecordEvent:
    """The :class:`~ter.ports.driving.EventIngest` for one short-lived hook process.

    Claude Code starts a hook process per event, so replaying the session's
    log into an engine on every hook would make each hook slower as the
    session grows. This use case only appends: its cost does not depend on
    the length of the session. It discards an event it has already recorded
    in this process; one recorded again by another process (a PostToolUse
    repeating its PreToolUse request, a retried hook) is dropped when the log
    is analysed, because the engine applies each event id once (the
    :class:`~ter.ports.driven.EventLog` port allows repeated records). Its
    signals carry acceptance only; the analysis is :meth:`report`, a fold of
    the log.
    """

    def __init__(self, tokenizer: Tokenizer, log: EventLog) -> None:
        self._tokenizer = tokenizer
        self._log = log
        self._recorded: set[EventId] = set()

    def apply(self, event: Event) -> Signals:
        if event.id in self._recorded:
            return Signals(event.id, accepted=False, raised=(Signal.DUPLICATE_EVENT,))
        self._log.append(event)
        self._recorded.add(event.id)
        return Signals(event.id, accepted=True)

    def report(self, session_id: str) -> StreamReport:
        return analyse_batch(self._log.events(session_id), self._tokenizer)


class AnalyseTrace:
    """Analyse a recorded session in batch: read it, then fold its events."""

    def __init__(self, source: SessionSource, tokenizer: Tokenizer) -> None:
        self._source = source
        self._tokenizer = tokenizer

    def __call__(self, ref: str | Path) -> StreamReport:
        trace = self._source.read(ref)
        report = analyse_batch(trace.events, self._tokenizer)
        if trace.usage_limits:
            report = replace(report, usage_limits=trace.usage_limits)
        return report


class AnalyseEventLog:
    """Analyse what live mode recorded for one session of an event log."""

    def __init__(self, log: EventLog, tokenizer: Tokenizer) -> None:
        self._log = log
        self._tokenizer = tokenizer

    def __call__(self, session_id: str) -> StreamReport:
        return analyse_batch(self._log.events(session_id), self._tokenizer)

    def sessions(self) -> tuple[str, ...]:
        return self._log.sessions()
