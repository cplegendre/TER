"""Structured detector signals: what L2 hands the L4 Intervention Engine.

The Intervention Engine (:mod:`ter.domain.intervention`) never reads events,
steps or findings (TER-INT-006). :func:`detector_signals` exports each
finding of an analysis as a :class:`~ter.domain.signal.Signal`, with
its evidence ids, its detector's published confidence rule, and the number
of tool events observed when it became known (its last cited event), which
is the clock that policy cooldowns count in.

:func:`control_signals` does the same for a session placed against its
control limits (TER-SPC-020): each firing control signal becomes a signal
whose detector is the control signal's id (``control.<measure>.<rule>``),
so a policy that names that id answers it like any detector, and whose
evidence is the sessions that form the pattern.
"""

from __future__ import annotations

from ..signal import Signal
from .analysis import LeanAnalysis
from .control import SessionControl

__all__ = ["CONTROL_KIND", "control_signals", "detector_signals", "tool_event_count"]

#: The ``kind`` of a signal from a control chart, not a finding.
CONTROL_KIND = "control"


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


def tool_event_count(analysis: LeanAnalysis) -> int:
    """The number of tool events observed: the clock signal positions use."""
    return sum(1 for step in analysis.steps if step.is_request)


def control_signals(control: SessionControl, position: int) -> tuple[Signal, ...]:
    """Every firing control signal of one session, as a signal (TER-SPC-020).

    Only signals that fire count: unfavourable, on a ratio or count measure,
    never a token or time total (TER-SPC-006, TER-INT-007). Limits computed
    with another detector set give none: a finding count no longer means
    what the limits measured (TER-SPC-007). An XmR rule is a published,
    deterministic test, so its confidence is 1.0 and its rule is the test.
    ``position`` is the tool event count when the session was placed.
    """
    if control.stale:
        return ()
    signals = []
    for placement in control.placements:
        cs = placement.signal
        if cs is None or not cs.fires:
            continue
        measure = placement.limits.measure
        signals.append(
            Signal(
                id=f"{cs.id}:{cs.session_id}",
                detector=cs.id,
                waste="",
                kind=CONTROL_KIND,
                confidence=1.0,
                title=(
                    f"{measure.label} {cs.value:g} is {cs.side.value} its "
                    f"limit {cs.limit:g}"
                ),
                subject=measure.key,
                evidence=cs.evidence,
                rule=cs.rule.description,
                position=position,
            )
        )
    return tuple(signals)
