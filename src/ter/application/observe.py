"""L1 use cases: observe a session live, or analyse a recorded one.

Every path into analysis goes through the
:class:`~ter.ports.driving.EventIngest` port (TER-OBS-001): a live hook applies
each event as it fires, and a recorded session (a transcript, a GARE run, a
replayed event log) is applied to a fresh ingest event by event before the
report is read. Both end in the same :class:`~ter.domain.stream.AnalysisEngine`,
so the live report and the batch report of one event stream are the same value.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from ..domain.events import Event, EventId
from ..domain.lean.analysis import LeanAnalysis, TerMeasure
from ..domain.lean.detectors import DEFAULT_REGISTRY, DetectorRegistry
from ..domain.lean.grounding import RepositoryGrounding
from ..domain.stream import (
    AnalysisEngine,
    Signal,
    Signals,
    StreamReport,
)
from ..ports.driven import EventLog, SessionSource, Tokenizer
from ..ports.driving import EventIngest

__all__ = [
    "AnalyseEventLog",
    "AnalyseTrace",
    "IngestFactory",
    "ObserveEvent",
    "RecordEvent",
    "fresh_ingest",
    "ingest_all",
]

#: Builds a fresh :class:`EventIngest` for one recorded session.
IngestFactory = Callable[[], EventIngest]


def ingest_all(ingest: EventIngest, events: Iterable[Event]) -> EventIngest:
    """Apply recorded events to ``ingest`` in order, as if they were live."""
    for event in events:
        ingest.apply(event)
    return ingest


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
        detectors: DetectorRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self._tokenizer = tokenizer
        self._log = log
        self._engine_factory = engine_factory
        self._detectors = detectors
        self._engines: dict[str, AnalysisEngine] = {}

    def apply(self, event: Event) -> Signals:
        engine = self._engine(event.session_id)
        if self._log is not None and event.id not in engine:
            self._log.append(event)
        return engine.apply(event)

    def report(self, session_id: str) -> StreamReport:
        return self._engine(session_id).snapshot()

    def explain(
        self,
        session_id: str,
        *,
        ter: TerMeasure | None = None,
        repository: RepositoryGrounding | None = None,
    ) -> LeanAnalysis:
        return self._engine(session_id).explain(
            ter=ter, registry=self._detectors, repository=repository
        )

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
    signals carry acceptance only; the analysis is :meth:`report`, the log
    replayed through an :class:`ObserveEvent`.
    """

    def __init__(
        self,
        tokenizer: Tokenizer,
        log: EventLog,
        detectors: DetectorRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self._tokenizer = tokenizer
        self._log = log
        self._detectors = detectors
        self._recorded: set[EventId] = set()

    def apply(self, event: Event) -> Signals:
        if event.id in self._recorded:
            return Signals(event.id, accepted=False, raised=(Signal.DUPLICATE_EVENT,))
        self._log.append(event)
        self._recorded.add(event.id)
        return Signals(event.id, accepted=True)

    def report(self, session_id: str) -> StreamReport:
        return self._replay(session_id).report(session_id)

    def explain(
        self,
        session_id: str,
        *,
        ter: TerMeasure | None = None,
        repository: RepositoryGrounding | None = None,
    ) -> LeanAnalysis:
        return self._replay(session_id).explain(
            session_id, ter=ter, repository=repository
        )

    def _replay(self, session_id: str) -> ObserveEvent:
        replay = ObserveEvent(self._tokenizer, detectors=self._detectors)
        ingest_all(replay, self._log.events(session_id))
        return replay


def fresh_ingest(tokenizer: Tokenizer, ingest: IngestFactory | None) -> IngestFactory:
    """``ingest``, or a factory of plain :class:`ObserveEvent` instances."""
    if ingest is not None:
        return ingest
    return lambda: ObserveEvent(tokenizer)


class AnalyseTrace:
    """Analyse a recorded session: read it, apply its events to a fresh
    :class:`EventIngest` in order, then read the report."""

    def __init__(
        self,
        source: SessionSource,
        tokenizer: Tokenizer,
        ingest: IngestFactory | None = None,
    ) -> None:
        self._source = source
        self._ingest = fresh_ingest(tokenizer, ingest)

    def __call__(self, ref: str | Path) -> StreamReport:
        trace = self._source.read(ref)
        ingest = ingest_all(self._ingest(), trace.events)
        report = ingest.report(trace.session_id)
        if trace.usage_limits:
            report = replace(report, usage_limits=trace.usage_limits)
        return report


class AnalyseEventLog:
    """Analyse what live mode recorded for one session of an event log, by
    replaying the log through a fresh :class:`EventIngest`."""

    def __init__(
        self,
        log: EventLog,
        tokenizer: Tokenizer,
        ingest: IngestFactory | None = None,
    ) -> None:
        self._log = log
        self._ingest = fresh_ingest(tokenizer, ingest)

    def __call__(self, session_id: str) -> StreamReport:
        ingest = ingest_all(self._ingest(), self._log.events(session_id))
        return ingest.report(session_id)

    def sessions(self) -> tuple[str, ...]:
        return self._log.sessions()
