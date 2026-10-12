"""Structured detector signals: what L2 hands the L4 Intervention Engine.

The Intervention Engine (:mod:`ter.domain.intervention`) never reads events,
steps or findings (TER-INT-006). :func:`detector_signals` exports each
finding of an analysis as a :class:`~ter.domain.intervention.Signal`, with
its evidence ids, its detector's published confidence rule, and the number
of tool events observed when it became known (its last cited event), which
is the clock that policy cooldowns count in.
"""

from __future__ import annotations

from ..intervention import Signal
from .analysis import LeanAnalysis

__all__ = ["detector_signals"]


def detector_signals(analysis: LeanAnalysis) -> tuple[Signal, ...]:
    """Every finding of ``analysis`` as a signal, in the order they became known."""
    tool_events = 0
    position: dict[str, int] = {}
    for step in analysis.steps:
        if step.is_request:
            tool_events += 1
        position[step.event_id] = tool_events
    rules = {i: rule for i, _, _, rule in analysis.detectors}
    signals = [
        Signal(
            id=f.id,
            detector=f.detector,
            waste=f.waste.value,
            kind=f.kind.value,
            confidence=f.confidence,
            title=f.title,
            subject=f.subject,
            evidence=tuple(f.evidence),
            rule=rules.get(f.detector, ""),
            position=max((position.get(e, 0) for e in f.evidence), default=0),
        )
        for f in analysis.findings
    ]
    return tuple(sorted(signals, key=lambda s: (s.position, s.id)))
