"""Use cases that orchestrate the domain through ports.

The TER 3 pipeline is still reached through the ``ter_calculator`` package
while its behaviour is frozen by the golden tests in ``tests/golden``. Use
cases move here one at a time as each capability is rebuilt against the
ports. L1 adds live observation and batch analysis of the event stream
(:mod:`ter.application.observe`); L2 adds the Lean explanation and the A3
(:mod:`ter.application.explain`). L3 records a session's TER 3 ratio and
outcome verdict as events and reports from the event log alone
(:mod:`ter.application.record`).
"""

from __future__ import annotations

from .explain import ExplainedSession, ExplainSession
from .observe import AnalyseEventLog, AnalyseTrace, ObserveEvent, RecordEvent
from .record import RecordedReport, RecordMeasures, explain_recorded

__all__ = [
    "AnalyseEventLog",
    "AnalyseTrace",
    "ExplainSession",
    "ExplainedSession",
    "ObserveEvent",
    "RecordEvent",
    "RecordMeasures",
    "RecordedReport",
    "explain_recorded",
]
