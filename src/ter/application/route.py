"""L3 use case: classify a recorded session's tasks and route them by role.

The router is advisory and offline (L3): it reads a recorded session, runs
the same analysis as ``explain`` (grounded on the repository when one is
given), classifies every task (TER-RTE-005) and decides a role for each from
the active routing profile (TER-RTE-001), escalating only on a detector
signal with evidence (TER-RTE-002, TER-RTE-003). The escalation events it
produces are returned for analysis and the A3's recommendations; nothing is
sent to a live session (TER-INT-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.events import SessionTrace
from ..domain.lean import LeanAnalysis
from ..domain.lean.detectors import SessionView
from ..domain.routing import RoutingPlan, classify_tasks, route_session
from ..ports.driven import (
    ArchitectureContracts,
    RepositoryEvidence,
    RoutingProfiles,
    SessionSource,
    Tokenizer,
)
from .ground import ground_session
from .observe import IngestFactory, fresh_ingest, ingest_all

__all__ = ["RouteSession", "RoutedSession"]


@dataclass(frozen=True)
class RoutedSession:
    trace: SessionTrace
    analysis: LeanAnalysis
    plan: RoutingPlan


class RouteSession:
    """Read a session, explain it, classify its tasks and route each by role."""

    def __init__(
        self,
        source: SessionSource,
        tokenizer: Tokenizer,
        profiles: RoutingProfiles,
        ingest: IngestFactory | None = None,
        repository: RepositoryEvidence | None = None,
        contracts: ArchitectureContracts | None = None,
    ) -> None:
        self._source = source
        self._profiles = profiles
        self._ingest = fresh_ingest(tokenizer, ingest)
        self._repository = repository
        self._contracts = contracts

    def __call__(self, ref: str | Path, profile: str | None = None) -> RoutedSession:
        active = self._profiles.profile(
            profile if profile is not None else self._profiles.default()
        )
        trace = self._source.read(ref)
        ingest = ingest_all(self._ingest(), trace.events)
        grounding = (
            ground_session(trace.events, self._repository, self._contracts)
            if self._repository is not None
            else None
        )
        analysis = (
            ingest.explain(trace.session_id)
            if grounding is None
            else ingest.explain(trace.session_id, repository=grounding)
        )
        view = SessionView.of(analysis.steps, analysis.intent, analysis.repository)
        tasks = classify_tasks(view, analysis.findings)
        plan = route_session(
            trace.session_id,
            analysis.steps,
            tasks,
            analysis.findings,
            active,
            # Escalation events are appended after the session's own events.
            first_sequence=len(trace.events),
        )
        return RoutedSession(trace, analysis, plan)
