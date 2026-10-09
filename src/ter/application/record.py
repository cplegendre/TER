"""L3 use cases: record a session's measures as events, and report from
the event log alone (TER-EXP-002).

:class:`RecordMeasures` appends an explained session to an
:class:`~ter.ports.driven.EventLog`: the session's own events that the log
does not hold yet, then its TER 3 ratio (``metric.recorded``) and its
outcome verdict (``verdict.recorded``) when it has them.
:func:`explain_recorded` rebuilds the explanation and the A3 from such a log
with nothing else: the fold picks the recorded ratio up, and the verdict is
judged again from the contract and evidence its record holds.

The price book is reference data shared by every session (TER-ANL-040),
not session input, so it is passed in. Two things are not recomputed: the
usage limits a source states (they come from the source and qualify
figures rather than being figures) and repository grounding (evidence about
the repository, read at the start commit, not events of the session).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.events import Event, EventKind
from ..domain.lean import A3Report, LeanAnalysis, build_a3
from ..domain.outcome import OutcomeVerdict, recorded_verdict, verdict_event
from ..domain.records import TER3_METRIC, metric_event
from ..ports.driven import EventLog, PriceBook, Tokenizer
from .explain import ExplainedSession
from .observe import IngestFactory, fresh_ingest, ingest_all

__all__ = ["RecordMeasures", "RecordedReport", "explain_recorded", "measure_events"]


def measure_events(explained: ExplainedSession) -> tuple[Event, ...]:
    """The records of an explained session's TER 3 ratio and outcome
    verdict, placed after its last event (none for a measure it lacks)."""
    session = explained.trace.events
    if not session:
        return ()
    out: list[Event] = []
    ter = explained.analysis.scorecard.ter
    if ter is not None:
        out.append(metric_event(session, TER3_METRIC, ter.value, ter.method, 1))
    if explained.outcome is not None:
        out.append(verdict_event(session, explained.outcome, len(out) + 1))
    return tuple(out)


class RecordMeasures:
    """Append an explained session, and its measures, to an event log."""

    def __init__(self, log: EventLog) -> None:
        self._log = log

    def __call__(self, explained: ExplainedSession) -> tuple[Event, ...]:
        """Append what the log lacks and return the records appended."""
        session_id = explained.trace.session_id
        held = {e.id for e in self._log.events(session_id)}
        for event in explained.trace.events:
            if event.id not in held:
                self._log.append(event)
                held.add(event.id)
        records = measure_events(explained)
        for record in records:
            if record.id not in held:
                self._log.append(record)
        return records


@dataclass(frozen=True)
class RecordedReport:
    """A session's explanation and A3, rebuilt from its event log."""

    analysis: LeanAnalysis
    a3: A3Report
    outcome: OutcomeVerdict | None


def explain_recorded(
    events: Sequence[Event],
    tokenizer: Tokenizer,
    prices: PriceBook | None = None,
    ingest: IngestFactory | None = None,
) -> RecordedReport:
    """Explain a session from its recorded events alone: they enter through
    ``EventIngest`` like a live or recorded session (TER-OBS-001), and the
    TER 3 ratio and the outcome verdict are read from their records."""
    if not events:
        raise ValueError("an event log with no events has no session to explain")
    fold = ingest_all(fresh_ingest(tokenizer, ingest)(), events)
    analysis = fold.explain(events[0].session_id)
    verdict = recorded_verdict(events)
    intents = tuple(e.text for e in events if e.kind is EventKind.PROMPT)
    return RecordedReport(
        analysis, build_a3(analysis, intents, verdict, prices=prices), verdict
    )
