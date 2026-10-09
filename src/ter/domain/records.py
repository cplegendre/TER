"""Measures TER records about a session, as events of that session (TER-EXP-002).

Two figures in a report are not folded from the agent's activity: the TER 3
ratio (scored from the source transcript by an embedder) and the outcome
verdict (judged from test evidence). To recompute every reported figure
from the event log alone, TER appends them to the session's log as
*records*: ``metric.recorded`` for a ratio, ``verdict.recorded`` for a
verdict (:mod:`ter.domain.outcome` encodes that one, so behaviour modules
never read it). A record carries its measure as JSON in ``text``, is
appended after the session's last event, and is no step, count or token
figure of the session (:attr:`EventKind.is_record`).

A record's id is derived from its session, kind and content, so recording
the same measure twice yields one id and a fold applies it once; a changed
measure is a new record, and the last one applied wins.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .events import Actor, Event, EventKind, Provenance, make_event_id

__all__ = [
    "RECORD_SOURCE",
    "TER3_METRIC",
    "RecordedMetric",
    "metric_event",
    "read_metric",
    "record_event",
    "record_payload",
    "recorded_metric",
]

#: ``Provenance.source`` of every record: TER itself, not a harness.
RECORD_SOURCE = "ter"
#: The metric name of the TER 3 ratio.
TER3_METRIC = "ter3"


@dataclass(frozen=True)
class RecordedMetric:
    """One recorded figure: its name, value and how it was computed."""

    name: str
    value: float
    method: str


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def record_event(
    session: Sequence[Event],
    kind: EventKind,
    payload: Mapping[str, object],
    offset: int = 1,
) -> Event:
    """A record of ``kind`` carrying ``payload``, placed ``offset`` after the
    last event of ``session`` (its sequence, and its time when known)."""
    if not kind.is_record:
        raise ValueError(f"{kind.value} is not a record kind")
    if not session:
        raise ValueError("a record needs the session it is about")
    last = max(session, key=lambda e: e.sequence)
    text = _canonical(payload)
    return Event(
        id=make_event_id(RECORD_SOURCE, last.session_id, kind.value, text),
        session_id=last.session_id,
        sequence=last.sequence + offset,
        kind=kind,
        actor=Actor.SYSTEM,
        text=text,
        provenance=Provenance(RECORD_SOURCE, f"{kind.value}:{payload.get('name', '')}"),
        timestamp=last.timestamp,
    )


def record_payload(event: Event) -> dict[str, object] | None:
    """The JSON object a record carries, or ``None`` when it carries none."""
    try:
        value = json.loads(event.text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def metric_event(
    session: Sequence[Event], name: str, value: float, method: str, offset: int = 1
) -> Event:
    """A ``metric.recorded`` event for one figure of ``session``."""
    payload = {"name": name, "value": value, "method": method}
    return record_event(session, EventKind.METRIC_RECORDED, payload, offset)


def read_metric(event: Event) -> RecordedMetric | None:
    """The figure a ``metric.recorded`` event carries; ``None`` for any other
    event or a record that cannot be read (never an error: a live fold
    must not fail on one bad record)."""
    if event.kind is not EventKind.METRIC_RECORDED:
        return None
    payload = record_payload(event)
    if payload is None:
        return None
    name, value, method = (payload.get(k) for k in ("name", "value", "method"))
    if not isinstance(name, str) or not isinstance(method, str):
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return RecordedMetric(name, float(value), method)


def recorded_metric(events: Iterable[Event], name: str) -> RecordedMetric | None:
    """The last recorded figure called ``name`` among ``events``."""
    found: RecordedMetric | None = None
    for event in events:
        metric = read_metric(event)
        if metric is not None and metric.name == name:
            found = metric
    return found
