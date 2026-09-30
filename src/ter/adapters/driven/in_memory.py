"""In-memory adapters: fakes for tests that must pass the same contract suites.

A fake that drifts from the real adapter's obligations fails
``tests/contract``, so tests that use these fakes stay honest.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from ...domain.events import Event, SessionTrace
from ...domain.pricing import PriceEntry, PriceSchedule, Rates


class InMemorySessionSource:
    """Serves pre-built traces by reference, renumbering sequences on the way out."""

    format_name = "in-memory"

    def __init__(self, traces: Mapping[str, SessionTrace]) -> None:
        self._traces = dict(traces)

    def read(self, ref: str | Path) -> SessionTrace:
        key = str(ref)
        if key not in self._traces:
            raise FileNotFoundError(f"No in-memory session named {key!r}")
        trace = self._traces[key]
        events: tuple[Event, ...] = tuple(
            replace(event, sequence=index) for index, event in enumerate(trace.events)
        )
        return replace(trace, events=events, source_format=self.format_name)


class InMemoryPriceBook:
    """Serves prices from entries built in code, with the real book's semantics."""

    name = "in-memory"

    def __init__(self, entries: Iterable[PriceEntry]) -> None:
        self._schedule = PriceSchedule(entries)

    def models(self) -> tuple[str, ...]:
        return self._schedule.models()

    def rate(self, model: str, at: date | None = None) -> Rates:
        return self._schedule.rate(model, at)


class FixedClock:
    """A clock that returns a fixed time, advanced explicitly by tests."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class SystemClock:
    """The real wall clock."""

    def now(self) -> datetime:
        return datetime.now().astimezone()


class InMemoryEventLog:
    """An :class:`~ter.ports.driven.EventLog` held in a dict, for tests."""

    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = {}

    def append(self, event: Event) -> None:
        self._events.setdefault(event.session_id, []).append(event)

    def events(self, session_id: str) -> tuple[Event, ...]:
        return tuple(self._events.get(session_id, ()))

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(self._events))


class FixedTerScorer:
    """A :class:`~ter.ports.driven.TerScorer` fake returning preset scores by reference."""

    def __init__(self, scores: Mapping[str, float], method: str = "fixed") -> None:
        self._scores = dict(scores)
        self.method = method

    def score(self, ref: str | Path) -> float:
        key = str(ref)
        if key not in self._scores:
            raise FileNotFoundError(f"No fixed TER for {key!r}")
        return self._scores[key]
