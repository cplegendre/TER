"""The grounded Evidence Graph of a session (L3, TER-GRF-001, points 71, 72, 77).

The L2 graph (:mod:`.graph`) links events by stage. This one gives every
event a **role** and links the roles with typed edges, each from one
published rule (``EDGE_RULES``), and joins the repository's evidence of which
later events used a read (:mod:`.usage`, TER-EVD-008).

Roles (nodes are events):

* ``intent``: a prompt.
* ``observation``: a tool result (a file's text, search hits, command
  output, a check's result).
* ``agent_decision``: a reasoning block or a planning tool call.
* ``action``: any other tool request (reads, searches, fetches, handoffs,
  shell commands that neither change files nor run a check).
* ``change``: an edit or write request.
* ``validation``: a request that runs a check.
* ``response``: a response to the developer, or a failed model route.

Edges point from the later event to the earlier one it rests on, so
following them back from a change reconstructs how it emerged (point 77):
see :meth:`GroundedEvidenceGraph.reconstruct`. The graph is deterministic:
nodes in session order, edges sorted by source, target and type.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..events import EventId, EventKind, ToolKind
from .model import Outcome, Step
from .usage import EvidenceUsage

__all__ = [
    "EDGE_RULES",
    "GRAPH_SCHEMA",
    "GraphEdge",
    "GraphEdgeType",
    "GraphNode",
    "GroundedEvidenceGraph",
    "NodeRole",
    "build_grounded_graph",
    "role_of",
]

GRAPH_SCHEMA = "ter.evidence-graph/1"


class NodeRole(StrEnum):
    INTENT = "intent"
    OBSERVATION = "observation"
    #: Not the bare word: that is a hook response field TER never writes
    #: (TER-INT-001).
    DECISION = "agent_decision"
    ACTION = "action"
    CHANGE = "change"
    VALIDATION = "validation"
    RESPONSE = "response"


class GraphEdgeType(StrEnum):
    OBSERVES = "observes"
    PURSUES = "pursues"
    BASED_ON = "based_on"
    DECIDED_BY = "decided_by"
    MOTIVATED_BY = "motivated_by"
    USES = "uses"
    VALIDATES = "validates"
    CORRECTS = "corrects"


#: The one rule each edge type is drawn by.
EDGE_RULES: Mapping[GraphEdgeType, str] = {
    GraphEdgeType.OBSERVES: (
        "observation -> the action, change or validation request whose result it is"
    ),
    GraphEdgeType.PURSUES: (
        "every decision, action, change, validation and response -> the "
        "intent (prompt) in force"
    ),
    GraphEdgeType.BASED_ON: (
        "decision -> every observation since the previous decision or prompt "
        "in the same task"
    ),
    GraphEdgeType.DECIDED_BY: (
        "action, change or validation -> the latest decision since the prompt in force"
    ),
    GraphEdgeType.MOTIVATED_BY: (
        "action, change, validation or response -> the latest observation "
        "since the prompt in force"
    ),
    GraphEdgeType.USES: (
        "a later event -> the read whose content it used, by the evidence "
        "usage rules (repository evidence, TER-EVD-008)"
    ),
    GraphEdgeType.VALIDATES: (
        "validation -> every change since the previous validation"
    ),
    GraphEdgeType.CORRECTS: (
        "change -> the latest failed check result not yet followed by a passing one"
    ),
}


@dataclass(frozen=True)
class GraphNode:
    event_id: EventId
    index: int
    role: NodeRole
    kind: EventKind
    tool: str | None
    path: str | None


@dataclass(frozen=True)
class GraphEdge:
    source: EventId
    target: EventId
    type: GraphEdgeType


@dataclass(frozen=True)
class GroundedEvidenceGraph:
    session_id: str | None
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    def node(self, event_id: EventId) -> GraphNode:
        return next(n for n in self.nodes if n.event_id == event_id)

    def edges_of(self, edge_type: GraphEdgeType) -> tuple[GraphEdge, ...]:
        return tuple(e for e in self.edges if e.type is edge_type)

    def out(self, event_id: EventId) -> tuple[GraphEdge, ...]:
        return tuple(e for e in self.edges if e.source == event_id)

    def reconstruct(self, event_id: EventId) -> tuple[GraphNode, ...]:
        """Every event ``event_id`` rests on, directly or transitively, then
        the event itself, in session order: how it emerged (point 77)."""
        targets: dict[EventId, list[EventId]] = {}
        for edge in self.edges:
            targets.setdefault(edge.source, []).append(edge.target)
        seen = {event_id}
        stack = [event_id]
        while stack:
            for target in targets.get(stack.pop(), ()):
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        return tuple(n for n in self.nodes if n.event_id in seen)

    def as_dict(self) -> dict[str, object]:
        roles = {r.value: 0 for r in NodeRole}
        for n in self.nodes:
            roles[n.role.value] += 1
        types = {t.value: 0 for t in GraphEdgeType}
        for e in self.edges:
            types[e.type.value] += 1
        return {
            "schema": GRAPH_SCHEMA,
            "session_id": self.session_id,
            "rules": {t.value: r for t, r in EDGE_RULES.items()},
            "roles": roles,
            "edge_types": types,
            "nodes": [
                {
                    "id": n.event_id,
                    "index": n.index,
                    "role": n.role.value,
                    "kind": n.kind.value,
                    "tool": n.tool,
                    "path": n.path,
                }
                for n in self.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target, "type": e.type.value}
                for e in self.edges
            ],
        }


def role_of(step: Step) -> NodeRole:
    """The role of one event in the graph."""
    if step.kind is EventKind.PROMPT:
        return NodeRole.INTENT
    if step.is_completion:
        return NodeRole.OBSERVATION
    if step.kind is EventKind.REASONING or (
        step.is_request and step.tool_kind is ToolKind.PLAN
    ):
        return NodeRole.DECISION
    if step.is_edit:
        return NodeRole.CHANGE
    if step.is_request and step.checks:
        return NodeRole.VALIDATION
    if step.is_request:
        return NodeRole.ACTION
    return NodeRole.RESPONSE


def build_grounded_graph(
    session_id: str | None,
    steps: Sequence[Step],
    usage: EvidenceUsage | None = None,
) -> GroundedEvidenceGraph:
    """Build the graph from steps and, at L3, the evidence usage of reads."""
    edges: set[tuple[int, int, GraphEdgeType]] = set()
    roles = [role_of(s) for s in steps]

    def link(source: int, target: int | None, edge_type: GraphEdgeType) -> None:
        if target is not None and target < source:
            edges.add((source, target, edge_type))

    prompt: int | None = None
    decision: int | None = None
    observation: int | None = None
    pending: list[int] = []  # observations since the previous decision
    changes: list[int] = []  # changes since the previous validation
    failure: int | None = None
    for step, role in zip(steps, roles, strict=True):
        i = step.index
        if role is NodeRole.INTENT:
            prompt, decision, observation = i, None, None
            pending = []
            continue
        if role is NodeRole.OBSERVATION:
            link(i, step.request_index, GraphEdgeType.OBSERVES)
            observation = i
            pending.append(i)
            if step.outcome is Outcome.FAILED:
                failure = i
            elif step.outcome is Outcome.PASSED:
                failure = None
            continue
        link(i, prompt, GraphEdgeType.PURSUES)
        if role is NodeRole.DECISION:
            for o in pending:
                link(i, o, GraphEdgeType.BASED_ON)
            pending = []
            decision = i
            continue
        link(i, observation, GraphEdgeType.MOTIVATED_BY)
        if role is NodeRole.RESPONSE:
            continue
        link(i, decision, GraphEdgeType.DECIDED_BY)
        if role is NodeRole.CHANGE:
            link(i, failure, GraphEdgeType.CORRECTS)
            changes.append(i)
        elif role is NodeRole.VALIDATION:
            for c in changes:
                link(i, c, GraphEdgeType.VALIDATES)
            changes = []

    if usage is not None:
        index = {s.event_id: s.index for s in steps}
        for read in usage.reads:
            target = index[read.result if read.result else read.event_id]
            for use in read.uses:
                link(index[use.event_id], target, GraphEdgeType.USES)

    nodes = tuple(
        GraphNode(
            event_id=s.event_id,
            index=s.index,
            role=role,
            kind=s.kind,
            tool=s.tool_kind.value if s.tool_kind else None,
            path=s.paths[0] if s.paths else None,
        )
        for s, role in zip(steps, roles, strict=True)
    )
    ordered = tuple(
        GraphEdge(steps[a].event_id, steps[b].event_id, t)
        for a, b, t in sorted(edges, key=lambda e: (e[0], e[1], e[2].value))
    )
    return GroundedEvidenceGraph(session_id, nodes, ordered)
