"""Lean concepts mapped to the agent-behaviour measures TER computes (point 12).

Each of the ten Lean concepts TER-LEN-006 names maps to at least one measure
that the code actually computes. A measure is either a detector (its findings,
``detector:<id>``) or a field of the A3 JSON (``a3:<dotted.path>``), so every
row can be checked against a real report: the tests resolve every path in an
A3 and look every detector up in the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["LEAN_MEASURES", "ConceptMeasure", "LeanConcept"]


class LeanConcept(StrEnum):
    VALUE = "value"
    FLOW = "flow"
    PULL = "pull"
    WIP = "wip"
    QUEUES = "queues"
    REWORK = "rework"
    DEFECTS = "defects"
    WAITING = "waiting"
    OVER_PROCESSING = "over_processing"
    MOTION = "motion"

    @property
    def label(self) -> str:
        return (
            "WIP"
            if self is LeanConcept.WIP
            else self.value.replace("_", "-").capitalize()
        )


@dataclass(frozen=True)
class ConceptMeasure:
    """One named measure of a Lean concept and what it means for an agent."""

    name: str
    source: str
    meaning: str

    @property
    def is_detector(self) -> bool:
        return self.source.startswith("detector:")

    @property
    def target(self) -> str:
        """The detector id or the A3 JSON path, without its prefix."""
        return self.source.split(":", 1)[1]

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "source": self.source, "meaning": self.meaning}


def _m(name: str, source: str, meaning: str) -> ConceptMeasure:
    return ConceptMeasure(name, source, meaning)


#: Every Lean concept, in the order TER-LEN-006 lists them, with its measures.
LEAN_MEASURES: dict[LeanConcept, tuple[ConceptMeasure, ...]] = {
    LeanConcept.VALUE: (
        _m(
            "Value-adding generated tokens",
            "a3:analysis.scorecard.activity_tokens.value_adding",
            "Edits that change the software and the final response.",
        ),
        _m(
            "Software Value Efficiency",
            "a3:analysis.scorecard.software_value_efficiency.tokens",
            "Value-adding work toward a verified outcome per unit of resource.",
        ),
    ),
    LeanConcept.FLOW: (
        _m(
            "Agentic flow efficiency (tokens)",
            "a3:analysis.scorecard.flow_efficiency_tokens",
            "Share of generated tokens progressing or recovering.",
        ),
        _m(
            "Agentic flow efficiency (time)",
            "a3:analysis.scorecard.flow_efficiency_time",
            "Share of agent wall time progressing or recovering.",
        ),
    ),
    LeanConcept.PULL: (
        _m(
            "Unused context",
            "detector:unused_context",
            "Context pushed into the window that no later action pulled.",
        ),
        _m(
            "Peak open hypotheses",
            "a3:analysis.wip.peak_by_kind.hypotheses",
            "Exploration not yet pulled into an action or a conclusion.",
        ),
    ),
    LeanConcept.WIP: (
        _m(
            "WIP after every event",
            "a3:analysis.wip.series",
            "Unresolved hypotheses, tasks, edits and failures per event.",
        ),
        _m(
            "Peak WIP",
            "a3:analysis.wip.peak.total",
            "The most unresolved work open at once.",
        ),
    ),
    LeanConcept.QUEUES: (
        _m(
            "Peak edits awaiting validation",
            "a3:analysis.wip.peak_by_kind.edits",
            "Changes queued behind the next check.",
        ),
        _m(
            "Peak open tasks",
            "a3:analysis.wip.peak_by_kind.tasks",
            "To-do items and subagent handoffs waiting to finish.",
        ),
    ),
    LeanConcept.REWORK: (
        _m(
            "Rework cycles",
            "detector:rework_cycle",
            "A fix that left the same check failing the same way.",
        ),
        _m(
            "Reworking tokens",
            "a3:analysis.scorecard.flow_tokens.reworking",
            "Generated tokens spent changing work again.",
        ),
    ),
    LeanConcept.DEFECTS: (
        _m(
            "Unvalidated implementation",
            "detector:unvalidated_implementation",
            "Edits answered without a check that covers them.",
        ),
        _m(
            "Premature implementation",
            "detector:premature_implementation",
            "Edits of code the agent had not seen.",
        ),
        _m(
            "Unresolved failures",
            "a3:analysis.wip.final.failures",
            "Failing checks never seen passing again.",
        ),
    ),
    LeanConcept.WAITING: (
        _m(
            "Waiting time",
            "a3:analysis.scorecard.flow_seconds.waiting",
            "Agent time blocked on a delegated agent that was not needed.",
        ),
        _m(
            "Unnecessary handoff",
            "detector:unnecessary_handoff",
            "Delegating work the agent then did itself.",
        ),
    ),
    LeanConcept.OVER_PROCESSING: (
        _m(
            "Repeated tool call",
            "detector:repeated_tool_call",
            "The same call returning the same output.",
        ),
        _m(
            "Repeated reasoning",
            "detector:repeated_reasoning",
            "Reasoning that restates earlier reasoning.",
        ),
        _m(
            "Excessive planning",
            "detector:excessive_planning",
            "A run of planning steps with no action.",
        ),
        _m(
            "Fragmented edits",
            "detector:fragmented_edits",
            "Many small edits to one file where one would do.",
        ),
    ),
    LeanConcept.MOTION: (
        _m(
            "Repeated exploration",
            "detector:repeated_exploration",
            "Re-reading a file or re-running a search with identical results.",
        ),
    ),
}
