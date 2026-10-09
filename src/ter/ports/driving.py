"""Driving ports: what the TER core offers to the outside world.

Driving adapters (hooks, CLI, later the CI gate) call TER only through these
Protocols, so a new entry point never reaches into the domain directly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.events import Event
from ..domain.lean.analysis import LeanAnalysis, TerMeasure
from ..domain.lean.grounding import RepositoryGrounding
from ..domain.stream import Signals, StreamReport


@runtime_checkable
class EventIngest(Protocol):
    """Accepts normalised events one at a time, as they happen or as recorded.

    It is the only way events enter analysis (TER-OBS-001): live hooks apply
    each event as it fires, and the recorded paths (a Claude Code transcript,
    a GARE run, a replayed event log) apply each event of the recording in
    order, then ask for the report or the explanation.

    Obligations, verified by ``tests/contract/test_event_ingest.py``:

    * applying an event whose id was already applied changes nothing and
      returns ``Signals.accepted == False`` (TER-OBS-004);
    * the report after applying a stream one event at a time equals the
      batch analysis of the same stream (TER-ANL-010);
    * events of different sessions are analysed separately;
    * the explanation after applying a stream equals the batch explanation
      of the same stream (TER-ANL-010);
    * ``explain`` given repository evidence (``repository``, L3) grounds
      the explanation on it, and without it explains exactly as L2.
    """

    def apply(self, event: Event) -> Signals: ...

    def report(self, session_id: str) -> StreamReport: ...

    def explain(
        self,
        session_id: str,
        *,
        ter: TerMeasure | None = None,
        repository: RepositoryGrounding | None = None,
    ) -> LeanAnalysis: ...
