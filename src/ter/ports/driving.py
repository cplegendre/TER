"""Driving ports: what the TER core offers to the outside world.

Driving adapters (hooks, CLI, later the CI gate) call TER only through these
Protocols, so a new entry point never reaches into the domain directly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.events import Event
from ..domain.stream import Signals, StreamReport


@runtime_checkable
class EventIngest(Protocol):
    """Accepts normalised events one at a time, as they happen.

    Obligations, verified by ``tests/contract/test_event_ingest.py``:

    * applying an event whose id was already applied changes nothing and
      returns ``Signals.accepted == False`` (TER-OBS-004);
    * the report after applying a stream one event at a time equals the
      batch analysis of the same stream (TER-ANL-010);
    * events of different sessions are analysed separately.
    """

    def apply(self, event: Event) -> Signals: ...

    def report(self, session_id: str) -> StreamReport: ...
