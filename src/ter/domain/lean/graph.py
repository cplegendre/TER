"""The Evidence Graph of one session (points 71 to 77, within session scope).

Nodes are events. Edges are typed and point from the later event to the
earlier one it rests on:

* ``completes``: a tool result completes its request.
* ``motivated_by``: an action (reasoning, tool call, response) rests on the
  observation or prompt just before it (point 73); an edit also rests on the
  latest read of its file and on the prompt in force (point 74).
* ``validates``: a validation run covers every edit since the previous run
  (point 75).
* ``corrects``: an edit made after a failed run corrects that failure
  (point 76).
* ``repeats``: a call, read or reasoning block repeats an earlier one, as a
  finding established.

Following the edges back from the last response reconstructs how the change
emerged (point 77). Repository evidence (symbols, tests, diffs) joins at L3.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..events import EventId, EventKind, ToolKind
from .model import ActivityClass, Finding, Outcome, Step

__all__ = [
    "EDGE_SCHEMA",
    "EdgeType",
    "EvidenceEdge",
    "EvidenceGraph",
    "EvidenceNode",
    "build_graph",
]

EDGE_SCHEMA = "ter.evidence/0.1"


class EdgeType(StrEnum):
    COMPLETES = "completes"
    MOTIVATED_BY = "motivated_by"
    VALIDATES = "validates"
    CORRECTS = "corrects"
    REPEATS = "repeats"


@dataclass(frozen=True)
class EvidenceNode:
    event_id: EventId
    index: int
    kind: EventKind
    stage: str
    activity_class: ActivityClass | None
    tool: str | None
    label: str


@dataclass(frozen=True)
class EvidenceEdge:
    source: EventId
    target: EventId
    type: EdgeType


@dataclass(frozen=True)
class EvidenceGraph:
    session_id: str | None
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]

    def edges_of(self, edge_type: EdgeType) -> tuple[EvidenceEdge, ...]:
        return tuple(e for e in self.edges if e.type is edge_type)

    def ancestors(self, event_id: EventId) -> tuple[EventId, ...]:
        """Every event ``event_id`` rests on, directly or transitively, in session order."""
        out: dict[EventId, list[EventId]] = {}
        for edge in self.edges:
            out.setdefault(edge.source, []).append(edge.target)
        seen: set[EventId] = set()
        stack = [event_id]
        while stack:
            for target in out.get(stack.pop(), ()):
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        index = {n.event_id: n.index for n in self.nodes}
        return tuple(sorted(seen, key=lambda e: index[e]))

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": EDGE_SCHEMA,
            "session_id": self.session_id,
            "nodes": [
                {
                    "id": n.event_id,
                    "index": n.index,
                    "kind": n.kind.value,
                    "stage": n.stage,
                    "activity_class": n.activity_class.value
                    if n.activity_class
                    else None,
                    "tool": n.tool,
                    "label": n.label,
                }
                for n in self.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target, "type": e.type.value}
                for e in self.edges
            ],
        }


def _label(step: Step) -> str:
    if step.is_request or step.is_completion:
        name = step.native_name or (step.tool_kind.value if step.tool_kind else "tool")
        subject = " ".join(step.subject.split())
        subject = subject if len(subject) <= 48 else subject[:47] + "…"
        suffix = (
            f" ({step.outcome.value})"
            if step.outcome and step.outcome is not Outcome.UNKNOWN
            else ""
        )
        prefix = "result: " if step.is_completion else ""
        return f"{prefix}{name} {subject}".strip() + suffix
    return step.kind.value


def build_graph(
    session_id: str | None,
    steps: Sequence[Step],
    findings: Iterable[Finding],
    classes: dict[EventId, ActivityClass | None],
) -> EvidenceGraph:
    """Build the graph from steps and the findings that establish repeats."""
    edges: dict[tuple[EventId, EventId, EdgeType], None] = {}

    def link(source: Step, target: Step | None, edge_type: EdgeType) -> None:
        if target is not None and target.index < source.index:
            edges[(source.event_id, target.event_id, edge_type)] = None

    observation: Step | None = None
    prompt: Step | None = None
    last_read: dict[str, Step] = {}
    edits_since_run: list[Step] = []
    failure: Step | None = None
    for step in steps:
        if step.kind is EventKind.PROMPT:
            prompt = observation = step
            continue
        if step.is_completion:
            if step.request_index is not None:
                link(step, steps[step.request_index], EdgeType.COMPLETES)
            observation = step
            if step.paths and step.tool_kind is ToolKind.FS_READ:
                last_read[step.paths[0]] = step
            if step.outcome is Outcome.FAILED:
                failure = step
            elif step.outcome is Outcome.PASSED:
                failure = None
            continue
        # Generated events.
        link(step, observation, EdgeType.MOTIVATED_BY)
        if step.is_edit:
            link(step, prompt, EdgeType.MOTIVATED_BY)
            for path in step.paths:
                link(step, last_read.get(path), EdgeType.MOTIVATED_BY)
            link(step, failure, EdgeType.CORRECTS)
            edits_since_run.append(step)
        elif step.is_validation:
            for edit in edits_since_run:
                link(step, edit, EdgeType.VALIDATES)
            edits_since_run = []

    by_id = {s.event_id: s for s in steps}
    for finding in findings:
        if finding.detector not in (
            "repeated_tool_call",
            "repeated_exploration",
            "repeated_reasoning",
        ):
            continue
        cited = [by_id[e] for e in finding.evidence if not by_id[e].is_completion]
        if len(cited) >= 2:
            link(cited[-1], cited[0], EdgeType.REPEATS)

    nodes = tuple(
        EvidenceNode(
            event_id=s.event_id,
            index=s.index,
            kind=s.kind,
            stage=s.stage.value,
            activity_class=classes.get(s.event_id),
            tool=s.tool_kind.value if s.tool_kind else None,
            label=_label(s),
        )
        for s in steps
    )
    ordered = sorted(
        (EvidenceEdge(src, dst, t) for src, dst, t in edges),
        key=lambda e: (by_id[e.source].index, by_id[e.target].index, e.type.value),
    )
    return EvidenceGraph(session_id, nodes, tuple(ordered))
